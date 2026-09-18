"""Storage layer: High-throughput SQLite persistence with batched commits.

This module manages the persistent storage tier for MDRAP, rigorously separating raw,
processed, and derived data structures in compliance with core architectural principles
(MDRAP Spec §5 & §26):
  - `canonical_events`: Fast queryable store of VALID and SUSPICIOUS ticks.
  - `quarantine`: SUSPICIOUS and INVALID records with raw payloads preserved for replay.
  - `lineage`: Immutable decision audit trail documenting source arbitrations and logic.
  - `source_health`: Real-time reliability and error rate snapshots per upstream feed.
  - `ohlcv_candles`: Aggregated multi-interval historical candles.
  - `spread_stats` & `volatility_stats`: Real-time microstructure summary statistics.
  - `consolidated_bbo`: National Best Bid and Offer top-of-book quotes.
  - `watchdog_alerts`: Anomaly alerts and automated remediation logs.

Performance & Concurrency Pragmas:
  - `PRAGMA journal_mode=WAL;`: Write-Ahead Logging allows concurrent readers without
    blocking active writers, and avoids disk sync stalls during hot writes.
  - `PRAGMA synchronous=NORMAL;`: Reduces fsync frequency while maintaining zero corruption
    guarantees in WAL mode.
  - `PRAGMA mmap_size=268435456;`: Memory-maps 256MB of database file directly into process
    virtual memory, bypassing operating system read/write syscall context switch overhead.
  - `PRAGMA cache_size=-64000;`: Allocates 64MB of dedicated RAM page cache.
  - `PRAGMA temp_store=MEMORY;`: Keeps transient sorting and index B-trees entirely in RAM.
  - `executemany`: Batched multi-row commits eliminate per-row filesystem sync overhead.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from models import CanonicalEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_events (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT,
    event_type TEXT,
    exchange_timestamp REAL,
    receive_timestamp REAL,
    processing_timestamp REAL,
    source TEXT,
    sequence_number INTEGER,
    price REAL,
    quantity REAL,
    bid_price REAL,
    bid_size REAL,
    ask_price REAL,
    ask_size REAL,
    quality_status TEXT,
    reasons TEXT,
    raw_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_canonical_instrument ON canonical_events(instrument_id, exchange_timestamp);
CREATE INDEX IF NOT EXISTS idx_canonical_covering ON canonical_events(instrument_id, exchange_timestamp, price, quantity);

CREATE TABLE IF NOT EXISTS quarantine (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT,
    source TEXT,
    quality_status TEXT,
    reasons TEXT,
    payload_json TEXT,
    receive_timestamp REAL
);

CREATE TABLE IF NOT EXISTS lineage (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT,
    source_event_ids TEXT,
    raw_id TEXT,
    transformations TEXT,
    validations_run TEXT,
    conflict INTEGER,
    decision_reason TEXT,
    chosen_source TEXT,
    code_version TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    total INTEGER,
    invalid INTEGER,
    suspicious INTEGER,
    duplicate INTEGER,
    gap INTEGER,
    ewma_latency_s REAL,
    score REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS ohlcv_candles (
    instrument_id TEXT,
    bucket_start REAL,
    interval_s REAL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    event_count INTEGER,
    PRIMARY KEY (instrument_id, bucket_start, interval_s)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_instrument ON ohlcv_candles(instrument_id, bucket_start);

CREATE TABLE IF NOT EXISTS spread_stats (
    instrument_id TEXT PRIMARY KEY,
    quote_count INTEGER,
    mean_spread REAL,
    min_spread REAL,
    max_spread REAL,
    crossed_count INTEGER,
    crossed_pct REAL
);

CREATE TABLE IF NOT EXISTS volatility_stats (
    instrument_id TEXT PRIMARY KEY,
    trade_count INTEGER,
    mean_price REAL,
    std_dev REAL,
    min_price REAL,
    max_price REAL,
    price_range_pct REAL
);

CREATE TABLE IF NOT EXISTS watchdog_alerts (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    alert_type TEXT,
    timestamp REAL,
    details TEXT,
    action_taken TEXT
);
CREATE INDEX IF NOT EXISTS idx_watchdog_source ON watchdog_alerts(source, timestamp);

CREATE TABLE IF NOT EXISTS consolidated_bbo (
    instrument_id TEXT PRIMARY KEY,
    best_bid REAL,
    best_bid_size REAL,
    best_bid_source TEXT,
    best_ask REAL,
    best_ask_size REAL,
    best_ask_source TEXT,
    spread REAL,
    mid_price REAL,
    is_crossed INTEGER,
    is_locked INTEGER,
    timestamp REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS consolidated_depth (
    instrument_id TEXT PRIMARY KEY,
    bids_json TEXT,
    asks_json TEXT,
    micro_price REAL,
    imbalance_ratio REAL,
    is_crossed INTEGER,
    timestamp REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL,
    actor TEXT,
    role TEXT,
    action TEXT,
    details TEXT,
    prev_hash TEXT,
    entry_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);

CREATE TABLE IF NOT EXISTS api_keys (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    rate_limit_eps REAL NOT NULL,
    can_access_l2 INTEGER NOT NULL,
    can_use_binary INTEGER NOT NULL,
    can_use_shm INTEGER NOT NULL,
    max_replay_events INTEGER NOT NULL,
    is_active INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_client ON api_keys(client_id);

CREATE TABLE IF NOT EXISTS vwap_curves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    mid_price REAL NOT NULL,
    best_bid REAL NOT NULL,
    best_ask REAL NOT NULL,
    curve_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vwap_curves_sym ON vwap_curves(instrument_id, timestamp);
"""


def _synchronized(method):
    """Thread-safe synchronization wrapper with SQLite busy retry backoff."""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            retries = 3
            while True:
                try:
                    return method(self, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() and retries > 0:
                        retries -= 1
                        time.sleep(0.05)
                    else:
                        raise

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


def _read_synchronized(method):
    """Thread-safe synchronization wrapper for read queries using decoupled read_conn."""

    def wrapper(self, *args, **kwargs):
        with self._read_lock:
            retries = 3
            while True:
                try:
                    return method(self, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() and retries > 0:
                        retries -= 1
                        time.sleep(0.01)
                    else:
                        raise

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class Store:
    """Thread-safe SQLite storage engine for MDRAP event stream and analytics."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        self._read_lock = threading.RLock()
        with self._lock:
            self.conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
            self.conn.execute("PRAGMA busy_timeout=5000;")
            try:
                self.conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass  # In-memory or read-only filesystems do not support WAL
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            # Memory-tuned pragmas: 64MB mmap and 16MB page cache by default (down from 256MB/64MB)
            mmap_mb = int(os.environ.get("MDRAP_SQLITE_MMAP_MB", 64))
            cache_mb = int(os.environ.get("MDRAP_SQLITE_CACHE_MB", 16))
            mmap_bytes = mmap_mb * 1024 * 1024
            cache_kib = cache_mb * 1000
            self.conn.execute(f"PRAGMA mmap_size={mmap_bytes};")
            self.conn.execute(f"PRAGMA cache_size=-{cache_kib};")
            self.conn.execute(
                "PRAGMA temp_store=MEMORY;"
            )  # In-memory temporary B-trees
            self.conn.executescript(SCHEMA)
            self.conn.commit()

            if path != ":memory:":
                try:
                    self.read_conn = sqlite3.connect(
                        path, timeout=30.0, check_same_thread=False
                    )
                    self.read_conn.execute("PRAGMA query_only=ON;")
                    self.read_conn.execute("PRAGMA busy_timeout=5000;")
                    self.read_conn.execute(f"PRAGMA mmap_size={mmap_bytes};")
                except Exception:
                    self.read_conn = self.conn
            else:
                self.read_conn = self.conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @_synchronized
    def write_canonical_batch(self, events: list[CanonicalEvent]):
        """Persist a batch of CanonicalEvents via executemany."""
        if not events:
            return
        self.conn.executemany(
            """INSERT OR REPLACE INTO canonical_events VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    e.event_id,
                    e.instrument_id,
                    e.event_type.value,
                    e.exchange_timestamp,
                    e.receive_timestamp,
                    e.processing_timestamp,
                    e.source,
                    e.sequence_number,
                    e.price,
                    e.quantity,
                    e.bid_price,
                    e.bid_size,
                    e.ask_price,
                    e.ask_size,
                    e.quality_status.value,
                    json.dumps(e.reasons) if e.reasons else "[]",
                    e.raw_id,
                )
                for e in events
            ],
        )

    @_synchronized
    def write_quarantine_batch(self, rows: list[tuple]):
        """Persist a batch of quarantined anomaly records."""
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO quarantine VALUES (?,?,?,?,?,?,?)",
            rows,
        )

    @_synchronized
    def write_lineage_batch(self, rows: list[tuple]):
        """Persist a batch of audit lineage records."""
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO lineage VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    @_synchronized
    def upsert_source_health(self, rows: list[tuple]):
        """Upsert feed health and composite reliability score rows."""
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )

    @_synchronized
    def commit(self):
        """Commit active database transaction."""
        self.conn.commit()

    # -- Query helpers (backs the CLI `query` subcommand / future API) --

    @_read_synchronized
    def latest(self, instrument_id: str, limit: int = 1):
        cur = self.read_conn.execute(
            """SELECT * FROM canonical_events WHERE instrument_id=?
               ORDER BY exchange_timestamp DESC LIMIT ?""",
            (instrument_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_events(
        self, instrument_id: str | None = None, limit: int = 1000
    ) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM canonical_events WHERE instrument_id=? ORDER BY exchange_timestamp DESC LIMIT ?",
                (instrument_id, limit),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM canonical_events ORDER BY exchange_timestamp DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def event_lineage(self, event_id: str) -> dict | None:
        cur = self.read_conn.execute(
            "SELECT * FROM lineage WHERE event_id=?", (event_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    @_read_synchronized
    def feed_health(self):
        cur = self.read_conn.execute("SELECT * FROM source_health")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def quarantine_sample(self, limit: int = 20):
        cur = self.read_conn.execute("SELECT * FROM quarantine LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_quarantine(self, limit: int = 50):
        return self.quarantine_sample(limit)

    @_read_synchronized
    def counts(self):
        cur = self.read_conn.execute(
            "SELECT quality_status, COUNT(*) FROM canonical_events GROUP BY quality_status"
        )
        return dict(cur.fetchall())

    @_synchronized
    def close(self):
        if (
            hasattr(self, "read_conn")
            and self.read_conn
            and self.read_conn != self.conn
        ):
            try:
                self.read_conn.close()
            except Exception:
                pass
            self.read_conn = None
        if hasattr(self, "conn") and self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    # -- Data lifecycle: retention & compaction --
    # ponytail: WORM tables (audit_log, lineage) are never touched — spec A3.

    @_synchronized
    def retention_compact(
        self, retain_days: int = 30, quarantine_days: int = 90
    ) -> dict:
        """Delete canonical_events older than retain_days, then reclaim disk.

        quarantine records are kept for quarantine_days (default 90) for
        evidentiary/audit compliance, while operational events prune at retain_days.
        Does NOT touch audit_log or lineage (append-only / WORM by design, spec A3).
        Returns dict with counts of deleted rows and compaction status.
        """
        cutoff = time.time() - (retain_days * 86400)
        q_cutoff = time.time() - (quarantine_days * 86400)
        cur = self.conn.execute(
            "DELETE FROM canonical_events WHERE exchange_timestamp < ?", (cutoff,)
        )
        deleted_canonical = cur.rowcount
        cur_q = self.conn.execute(
            "DELETE FROM quarantine WHERE receive_timestamp < ?", (q_cutoff,)
        )
        deleted_quarantine = cur_q.rowcount
        self.conn.commit()
        # Reclaim disk: checkpoint WAL then truncate
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception:
            pass  # :memory: or non-WAL mode
        return {
            "deleted_canonical": deleted_canonical,
            "deleted_quarantine": deleted_quarantine,
            "cutoff_ts": cutoff,
            "quarantine_cutoff_ts": q_cutoff,
        }

    @_synchronized
    def vacuum(self):
        """Full VACUUM to reclaim disk space after large deletions."""
        self.conn.execute("VACUUM;")
        return True

    # -- V3 Analytical write methods --

    @_synchronized
    def write_ohlcv_batch(self, candles: list[dict]) -> None:
        if not candles:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO ohlcv_candles VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    c["instrument_id"],
                    c["bucket_start"],
                    c["interval_s"],
                    c["open"],
                    c["high"],
                    c["low"],
                    c["close"],
                    c["volume"],
                    c["event_count"],
                )
                for c in candles
            ],
        )

    @_synchronized
    def write_spread_batch(self, spreads: list[dict]) -> None:
        if not spreads:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO spread_stats VALUES (?,?,?,?,?,?,?)",
            [
                (
                    s["instrument_id"],
                    s["quote_count"],
                    s["mean_spread"],
                    s["min_spread"],
                    s["max_spread"],
                    s["crossed_count"],
                    s["crossed_pct"],
                )
                for s in spreads
            ],
        )

    @_synchronized
    def write_volatility_batch(self, stats: list[dict]) -> None:
        if not stats:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO volatility_stats VALUES (?,?,?,?,?,?,?)",
            [
                (
                    v["instrument_id"],
                    v["trade_count"],
                    v["mean_price"],
                    v["std_dev"],
                    v["min_price"],
                    v["max_price"],
                    v["price_range_pct"],
                )
                for v in stats
            ],
        )

    # -- V3 Analytical query methods --

    @_read_synchronized
    def query_ohlcv(self, instrument_id: str = None, limit: int = 50) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM ohlcv_candles WHERE instrument_id=? ORDER BY bucket_start DESC LIMIT ?",
                (instrument_id, limit),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM ohlcv_candles ORDER BY instrument_id, bucket_start DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_spread(self, instrument_id: str = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM spread_stats WHERE instrument_id=?", (instrument_id,)
            )
        else:
            cur = self.read_conn.execute("SELECT * FROM spread_stats")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_volatility(self, instrument_id: str = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM volatility_stats WHERE instrument_id=?", (instrument_id,)
            )
        else:
            cur = self.read_conn.execute("SELECT * FROM volatility_stats")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Phase 7 Watchdog alert methods --

    @_read_synchronized
    def query_alerts(self, limit: int = 20) -> list[dict]:
        cur = self.read_conn.execute(
            "SELECT source, alert_type, timestamp, details, action_taken FROM watchdog_alerts ORDER BY rowid DESC LIMIT ?",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Consolidated BBO methods --

    @_synchronized
    def write_bbo_batch(self, bbos: list) -> None:
        if not bbos:
            return
        now = time.time()
        rows = []
        for b in bbos:
            if hasattr(b, "instrument_id"):
                rows.append(
                    (
                        b.instrument_id,
                        b.best_bid,
                        b.best_bid_size,
                        b.best_bid_source,
                        b.best_ask,
                        b.best_ask_size,
                        b.best_ask_source,
                        b.spread,
                        b.mid_price,
                        1 if b.is_crossed else 0,
                        1 if b.is_locked else 0,
                        b.timestamp,
                        now,
                    )
                )
            elif isinstance(b, dict):
                rows.append(
                    (
                        b["instrument_id"],
                        b["best_bid"],
                        b["best_bid_size"],
                        b["best_bid_source"],
                        b["best_ask"],
                        b["best_ask_size"],
                        b["best_ask_source"],
                        b["spread"],
                        b["mid_price"],
                        1 if b.get("is_crossed") else 0,
                        1 if b.get("is_locked") else 0,
                        b["timestamp"],
                        now,
                    )
                )
        self.conn.executemany(
            """INSERT OR REPLACE INTO consolidated_bbo VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )

    @_read_synchronized
    def query_bbo(self, instrument_id: str | None = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_bbo WHERE instrument_id=?", (instrument_id,)
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_bbo ORDER BY instrument_id ASC"
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Consolidated Level-2 Market Depth methods --

    @_synchronized
    def write_depth_batch(self, ladders: list) -> None:
        if not ladders:
            return
        now = time.time()
        rows = []
        for lad in ladders:
            if hasattr(lad, "instrument_id"):
                bids_json = json.dumps([b.to_dict() for b in lad.bids])
                asks_json = json.dumps([a.to_dict() for a in lad.asks])
                rows.append(
                    (
                        lad.instrument_id,
                        bids_json,
                        asks_json,
                        lad.micro_price,
                        lad.imbalance_ratio,
                        1 if lad.is_crossed else 0,
                        lad.timestamp,
                        now,
                    )
                )
            elif isinstance(lad, dict):
                rows.append(
                    (
                        lad["instrument_id"],
                        json.dumps(lad.get("bids", [])),
                        json.dumps(lad.get("asks", [])),
                        lad.get("micro_price", 0.0),
                        lad.get("imbalance_ratio", 0.0),
                        1 if lad.get("is_crossed") else 0,
                        lad.get("timestamp", now),
                        now,
                    )
                )
        self.conn.executemany(
            """INSERT OR REPLACE INTO consolidated_depth VALUES
               (?,?,?,?,?,?,?,?)""",
            rows,
        )

    @_read_synchronized
    def query_depth(self, instrument_id: str | None = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_depth WHERE instrument_id=?",
                (instrument_id,),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_depth ORDER BY instrument_id ASC"
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Real-Time VWAP Slicing & Liquidity Depth methods --

    @_synchronized
    def write_vwap_batch(self, curves: list) -> None:
        if not curves:
            return
        now = time.time()
        rows = []
        for c in curves:
            if hasattr(c, "instrument_id"):
                rows.append(
                    (
                        c.instrument_id,
                        c.timestamp,
                        c.mid_price,
                        c.best_bid,
                        c.best_ask,
                        json.dumps(c.to_dict()),
                        now,
                    )
                )
            elif isinstance(c, dict):
                rows.append(
                    (
                        c["instrument_id"],
                        c.get("timestamp", now),
                        c.get("mid_price", 0.0),
                        c.get("best_bid", 0.0),
                        c.get("best_ask", 0.0),
                        json.dumps(c),
                        now,
                    )
                )
        self.conn.executemany(
            """INSERT INTO vwap_curves (instrument_id, timestamp, mid_price, best_bid, best_ask, curve_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )

    @_read_synchronized
    def query_vwap_curves(
        self, instrument_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM vwap_curves WHERE instrument_id=? ORDER BY timestamp DESC LIMIT ?",
                (instrument_id, limit),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM vwap_curves ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_synchronized
    def write_audit_entry(
        self,
        timestamp: float,
        actor: str,
        role: str,
        action: str,
        details: str,
        prev_hash: str,
        entry_hash: str,
    ) -> None:
        self.conn.execute(
            """INSERT INTO audit_log (timestamp, actor, role, action, details, prev_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, actor, role, action, details, prev_hash, entry_hash),
        )

    @_synchronized
    def get_latest_audit_hash(self) -> str:
        cur = self.conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return (
            row[0]
            if row
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )

    @_synchronized
    def query_audit_log(self, limit: int = 50) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY entry_id DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_synchronized
    def verify_audit_integrity(self) -> tuple[bool, str, int]:
        import hashlib

        cur = self.conn.execute(
            "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash FROM audit_log ORDER BY entry_id ASC"
        )
        rows = cur.fetchall()
        if not rows:
            return True, "Audit log is empty (valid)", 0

        expected_prev = (
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        for entry_id, ts, actor, role, action, details, prev_h, entry_h in rows:
            if prev_h != expected_prev:
                return (
                    False,
                    f"Broken chain link at entry #{entry_id}: expected prev_hash '{expected_prev[:12]}...', got '{prev_h[:12]}...'",
                    entry_id,
                )
            esc_actor = str(actor).replace("|", r"\|")
            esc_role = str(role).replace("|", r"\|")
            esc_action = str(action).replace("|", r"\|")
            esc_details = str(details).replace("|", r"\|")
            payload_str = (
                f"{prev_h}|{ts:.6f}|{esc_actor}|{esc_role}|{esc_action}|{esc_details}"
            )
            recomputed_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            if recomputed_hash != entry_h:
                return (
                    False,
                    f"Tampered entry #{entry_id}: hash mismatch (stored '{entry_h[:12]}...', calculated '{recomputed_hash[:12]}...')",
                    entry_id,
                )
            expected_prev = entry_h

        return (
            True,
            f"Cryptographic audit chain verified ({len(rows)} entries intact)",
            len(rows),
        )

    @_synchronized
    def export_audit_proof(self, output_file: str | None = None) -> dict:
        """Export cryptographic audit trail as an independently verifiable JSON proof."""
        import json

        cur = self.conn.execute(
            "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash FROM audit_log ORDER BY entry_id ASC"
        )
        cols = [
            "entry_id",
            "timestamp",
            "actor",
            "role",
            "action",
            "details",
            "prev_hash",
            "entry_hash",
        ]
        entries = [dict(zip(cols, r)) for r in cur.fetchall()]

        latest_hash = (
            entries[-1]["entry_hash"]
            if entries
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        proof = {
            "version": "1.0.0",
            "specification": "MDRAP-Spec-19.3",
            "algorithm": "sha256",
            "genesis_hash": "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
            "total_entries": len(entries),
            "latest_hash": latest_hash,
            "entries": entries,
        }
        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(proof, f, indent=2)
        return proof

    @staticmethod
    def verify_standalone_proof(proof_data_or_path: Any) -> tuple[bool, str, int]:
        """Independently verify a JSON audit proof without database access."""
        import hashlib
        import json

        if isinstance(proof_data_or_path, str):
            with open(proof_data_or_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = proof_data_or_path

        entries = data.get("entries", [])
        if not entries:
            return True, "Audit proof is empty (valid)", 0

        expected_prev = data.get(
            "genesis_hash",
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
        )
        for item in entries:
            entry_id = item["entry_id"]
            prev_h = item["prev_hash"]
            entry_h = item["entry_hash"]

            if prev_h != expected_prev:
                return (
                    False,
                    f"Broken chain link at entry #{entry_id}: expected prev '{expected_prev[:12]}...', got '{prev_h[:12]}...'",
                    entry_id,
                )

            esc_actor = str(item["actor"]).replace("|", r"\|")
            esc_role = str(item["role"]).replace("|", r"\|")
            esc_action = str(item["action"]).replace("|", r"\|")
            esc_details = str(item["details"]).replace("|", r"\|")
            payload_str = f"{prev_h}|{item['timestamp']:.6f}|{esc_actor}|{esc_role}|{esc_action}|{esc_details}"
            calc_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            if calc_hash != entry_h:
                return (
                    False,
                    f"Tampered entry #{entry_id}: hash mismatch (stored '{entry_h[:12]}...', calculated '{calc_hash[:12]}...')",
                    entry_id,
                )

            expected_prev = entry_h

        return (
            True,
            f"Independent cryptographic audit proof verified ({len(entries)} entries intact)",
            len(entries),
        )

    @_synchronized
    def save_api_key(self, ent: Any) -> None:
        """Save or update a client API key entitlement."""
        tier_val = getattr(ent, "tier", "STANDARD")
        tier_str = tier_val.value if hasattr(tier_val, "value") else str(tier_val)
        self.conn.execute(
            """INSERT OR REPLACE INTO api_keys
               (token, client_id, tier, rate_limit_eps, can_access_l2, can_use_binary, can_use_shm, max_replay_events, is_active, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ent.token,
                ent.client_id,
                tier_str,
                float(ent.rate_limit_eps),
                1 if getattr(ent, "can_access_l2", True) else 0,
                1 if getattr(ent, "can_use_binary", True) else 0,
                1 if getattr(ent, "can_use_shm", True) else 0,
                int(getattr(ent, "max_replay_events", 100_000)),
                1 if ent.is_active else 0,
                float(ent.created_at),
                float(ent.expires_at) if ent.expires_at is not None else None,
            ),
        )
        self.conn.commit()

    @_synchronized
    def load_api_keys(self) -> list:
        """Load all registered API keys from the store."""
        from security import ClientEntitlement, Tier

        cur = self.conn.execute(
            """SELECT token, client_id, tier, rate_limit_eps, can_access_l2, can_use_binary, can_use_shm, max_replay_events, is_active, created_at, expires_at
               FROM api_keys"""
        )
        results = []
        for row in cur.fetchall():
            results.append(
                ClientEntitlement(
                    token=row[0],
                    client_id=row[1],
                    tier=Tier.STANDARD,
                    rate_limit_eps=float(row[3]),
                    can_access_l2=bool(row[4]),
                    can_use_binary=bool(row[5]),
                    can_use_shm=bool(row[6]),
                    max_replay_events=int(row[7]),
                    is_active=bool(row[8]),
                    created_at=float(row[9]),
                    expires_at=float(row[10]) if row[10] is not None else None,
                )
            )
        return results

    @_synchronized
    def revoke_api_key(self, token: str) -> bool:
        """Mark an API key as inactive."""
        cur = self.conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE token = ?", (token,)
        )
        self.conn.commit()
        return cur.rowcount > 0
