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

from contextlib import contextmanager
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from models import CanonicalEvent

__stability__ = "stable"

logger = logging.getLogger("mdrap.storage")

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
CREATE INDEX IF NOT EXISTS idx_canonical_covering ON canonical_events(instrument_id, exchange_timestamp, price, quantity);
CREATE INDEX IF NOT EXISTS idx_canonical_proc_ts ON canonical_events(processing_timestamp);
CREATE INDEX IF NOT EXISTS idx_canonical_src_seq ON canonical_events(source, sequence_number);
CREATE INDEX IF NOT EXISTS idx_canonical_exch_ts ON canonical_events(exchange_timestamp DESC);

CREATE TABLE IF NOT EXISTS quarantine (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT,
    source TEXT,
    quality_status TEXT,
    reasons TEXT,
    payload_json TEXT,
    receive_timestamp REAL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_recv_ts ON quarantine(receive_timestamp);

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
    entry_hash TEXT,
    format_version INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);

CREATE TABLE IF NOT EXISTS api_keys (
    token_hash TEXT PRIMARY KEY,
    key_prefix TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'VIEWER',
    is_active INTEGER NOT NULL DEFAULT 1,
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

CREATE TABLE IF NOT EXISTS quarantine_merkle_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    entry_hash TEXT NOT NULL,
    prev_root TEXT NOT NULL,
    batch_root TEXT NOT NULL,
    batch_size INTEGER NOT NULL,
    format_version INTEGER NOT NULL DEFAULT 3
);
CREATE INDEX IF NOT EXISTS idx_quarantine_merkle_ts ON quarantine_merkle_log(timestamp);
"""


# ---------------------------------------------------------------------------
# Storage Tier & SQLite Concurrency Tuning Constants (Spec §5 & §26)
# ---------------------------------------------------------------------------
DEFAULT_SQLITE_RETRIES: int = 3
SQLITE_RETRY_BACKOFF_WRITE_S: float = 0.05  # 50 ms backoff on write lock contention
SQLITE_RETRY_BACKOFF_READ_S: float = 0.01  # 10 ms backoff on read contention
DEFAULT_SQLITE_TIMEOUT_S: float = 30.0  # 30-second busy timeout
DEFAULT_BUSY_TIMEOUT_MS: int = 30000  # 30,000 ms pragma busy timeout
DEFAULT_SQLITE_MMAP_MB: int = 64  # 64 MB mmap
DEFAULT_SQLITE_CACHE_MB: int = 16  # 16 MB dedicated cache
BYTES_PER_MB: int = 1024 * 1024
PAGE_CACHE_KIB_PER_MB: int = 1000


def _compute_merkle_root(leaf_hashes: list[bytes]) -> str:
    """Pairwise SHA-256 Merkle root computation over leaf byte hashes."""
    if not leaf_hashes:
        return hashlib.sha256(b"EMPTY_BATCH").hexdigest()
    current = list(leaf_hashes)
    while len(current) > 1:
        next_level = []
        for i in range(0, len(current), 2):
            if i + 1 < len(current):
                pair = current[i] + current[i + 1]
            else:
                pair = current[i] + current[i]  # duplicate last odd leaf
            next_level.append(hashlib.sha256(pair).digest())
        current = next_level
    return current[0].hex()


def _synchronized(method):
    """Thread-safe synchronization wrapper with SQLite busy retry backoff."""

    def wrapper(self, *args, **kwargs):
        retries = DEFAULT_SQLITE_RETRIES
        while True:
            with self._lock:
                try:
                    return method(self, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or retries <= 0:
                        raise
                    retries -= 1
            time.sleep(SQLITE_RETRY_BACKOFF_WRITE_S)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


def _read_synchronized(method):
    """Thread-safe synchronization wrapper for read queries using decoupled read_conn."""

    def wrapper(self, *args, **kwargs):
        retries = DEFAULT_SQLITE_RETRIES
        while True:
            with self._read_lock:
                try:
                    return method(self, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or retries <= 0:
                        raise
                    retries -= 1
            time.sleep(SQLITE_RETRY_BACKOFF_READ_S)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class Store:
    """Thread-safe SQLite storage engine for MDRAP event stream and analytics."""

    def __init__(self, path: str = ":memory:", durability: str = "balanced"):
        self.path = path
        self.durability = (durability or "balanced").lower()
        if self.durability not in ("fast", "balanced", "compliance"):
            self.durability = "balanced"
        self.conflicts: int = 0
        self._lock = threading.RLock()
        self._read_lock = threading.RLock()
        with self._lock:
            if path != ":memory:" and not path.startswith("file:"):
                dir_path = os.path.dirname(os.path.abspath(path))
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
            self.conn = sqlite3.connect(
                path, timeout=DEFAULT_SQLITE_TIMEOUT_S, check_same_thread=False
            )
            self.conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};")
            try:
                cur = self.conn.execute("PRAGMA journal_mode=WAL;")
                jm_row = cur.fetchone()
                if (
                    path != ":memory:"
                    and not path.startswith("file::memory:")
                    and jm_row
                    and jm_row[0].lower() != "wal"
                ):
                    logger.warning(
                        "SQLite database at %s could not set journal_mode=WAL (got %s)",
                        path,
                        jm_row[0],
                    )
            except sqlite3.OperationalError:
                pass  # In-memory or read-only filesystems do not support WAL

            if self.durability == "fast":
                self.conn.execute("PRAGMA synchronous=OFF;")
            elif self.durability == "compliance":
                self.conn.execute("PRAGMA synchronous=FULL;")
            else:
                self.conn.execute("PRAGMA synchronous=NORMAL;")

            # Memory-tuned pragmas: 64MB mmap and 16MB page cache by default
            mmap_mb = int(
                os.environ.get("MDRAP_SQLITE_MMAP_MB", DEFAULT_SQLITE_MMAP_MB)
            )
            cache_mb = int(
                os.environ.get("MDRAP_SQLITE_CACHE_MB", DEFAULT_SQLITE_CACHE_MB)
            )
            mmap_bytes = mmap_mb * BYTES_PER_MB
            cache_kib = cache_mb * PAGE_CACHE_KIB_PER_MB
            self.conn.execute(f"PRAGMA mmap_size={mmap_bytes};")
            self.conn.execute(f"PRAGMA cache_size=-{cache_kib};")
            self.conn.execute("PRAGMA temp_store=MEMORY;")
            self.conn.execute("PRAGMA user_version = 2;")
            self.conn.executescript(SCHEMA)
            try:
                self.conn.execute(
                    "ALTER TABLE audit_log ADD COLUMN format_version INTEGER NOT NULL DEFAULT 2"
                )
            except Exception:
                pass

            # Auto-migration for api_keys schema (from legacy 'token' column to 'token_hash' + 'role' + 'key_prefix')
            try:
                cur_cols = self.conn.execute("PRAGMA table_info(api_keys)").fetchall()
                col_names = {c[1] for c in cur_cols}
                if col_names and (
                    "token_hash" not in col_names
                    or "rate_limit_eps" in col_names
                    or "tier" in col_names
                ):
                    # Legacy table exists with older columns
                    self.conn.execute("ALTER TABLE api_keys RENAME TO api_keys_legacy")
                    self.conn.execute("""
                        CREATE TABLE api_keys (
                            token_hash TEXT PRIMARY KEY,
                            key_prefix TEXT NOT NULL DEFAULT '',
                            client_id TEXT NOT NULL,
                            role TEXT NOT NULL DEFAULT 'VIEWER',
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at REAL NOT NULL,
                            expires_at REAL
                        )
                    """)
                    self.conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_api_keys_client ON api_keys(client_id)"
                    )
                    cur_legacy = self.conn.execute("SELECT * FROM api_keys_legacy")
                    legacy_col_names = [d[0] for d in cur_legacy.description]
                    legacy_rows = cur_legacy.fetchall()
                    for r in legacy_rows:
                        row_dict = dict(zip(legacy_col_names, r))
                        raw_tok = str(row_dict.get("token") or "")
                        tok_hash = str(row_dict.get("token_hash") or "")
                        if not tok_hash and raw_tok:
                            tok_hash = hashlib.sha256(
                                raw_tok.encode("utf-8")
                            ).hexdigest()
                        if not tok_hash:
                            continue
                        pfx = str(row_dict.get("key_prefix") or "")
                        if not pfx and raw_tok:
                            pfx = raw_tok[:12] + "..." if len(raw_tok) > 12 else raw_tok
                        elif not pfx:
                            pfx = tok_hash[:10] + "..."
                        client_id = str(row_dict.get("client_id") or "Migrated_Client")
                        role = str(row_dict.get("role") or "VIEWER")
                        active = int(row_dict.get("is_active", 1))
                        created = float(row_dict.get("created_at") or time.time())
                        expires = row_dict.get("expires_at")
                        self.conn.execute(
                            """INSERT OR REPLACE INTO api_keys
                               (token_hash, key_prefix, client_id, role, is_active, created_at, expires_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (tok_hash, pfx, client_id, role, active, created, expires),
                        )
                    self.conn.execute("DROP TABLE api_keys_legacy")
                elif col_names:
                    if "role" not in col_names:
                        self.conn.execute(
                            "ALTER TABLE api_keys ADD COLUMN role TEXT NOT NULL DEFAULT 'VIEWER'"
                        )
                    if "key_prefix" not in col_names:
                        self.conn.execute(
                            "ALTER TABLE api_keys ADD COLUMN key_prefix TEXT NOT NULL DEFAULT ''"
                        )
            except Exception as e:
                import logging

                logging.getLogger("mdrap.storage").warning(
                    "Auto-migration notice: %s", e
                )
            self.conn.commit()

            if path != ":memory:":
                try:
                    self.read_conn = sqlite3.connect(
                        path, timeout=DEFAULT_SQLITE_TIMEOUT_S, check_same_thread=False
                    )
                    self.read_conn.execute("PRAGMA query_only=ON;")
                    self.read_conn.execute(
                        f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};"
                    )
                    self.read_conn.execute(f"PRAGMA mmap_size={mmap_bytes};")
                    self.read_conn.execute(f"PRAGMA cache_size=-{cache_kib};")
                    self.read_conn.execute("PRAGMA temp_store=MEMORY;")
                except Exception:
                    self.read_conn = self.conn
            else:
                self.read_conn = self.conn

    @contextmanager
    def transaction(self):
        """Explicit transaction context manager: BEGIN IMMEDIATE, commit on success, rollback on error."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @_synchronized
    def write_canonical_batch(self, events: list[CanonicalEvent]):
        """Persist a batch of CanonicalEvents via executemany with conflict preservation."""
        if not events:
            return
        c_before = self.conn.total_changes
        self.conn.executemany(
            """INSERT INTO canonical_events VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO NOTHING""",
            (
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
            ),
        )
        inserted = self.conn.total_changes - c_before
        if inserted < len(events):
            self.conflicts += len(events) - inserted

    @_synchronized
    def write_quarantine_batch(self, rows: list[tuple]):
        """Persist a batch of quarantined anomaly records with conflict preservation."""
        if not rows:
            return
        c_before = self.conn.total_changes
        self.conn.executemany(
            "INSERT INTO quarantine VALUES (?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
            rows,
        )
        inserted = self.conn.total_changes - c_before
        if inserted < len(rows):
            self.conflicts += len(rows) - inserted
        self._append_quarantine_merkle_batch_in_tx(rows)

    @_synchronized
    def write_lineage_batch(self, rows: list[tuple]):
        """Persist a batch of audit lineage records with conflict preservation."""
        if not rows:
            return
        c_before = self.conn.total_changes
        self.conn.executemany(
            "INSERT INTO lineage VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
            rows,
        )
        inserted = self.conn.total_changes - c_before
        if inserted < len(rows):
            self.conflicts += len(rows) - inserted

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
    def write_batches_atomic(
        self,
        canonical: list[CanonicalEvent] | None = None,
        quarantine: list[tuple] | None = None,
        lineage: list[tuple] | None = None,
        source_health: list[tuple] | None = None,
    ) -> None:
        """Atomic multi-batch write in a single BEGIN IMMEDIATE transaction."""
        with self.transaction():
            if canonical:
                c_before = self.conn.total_changes
                self.conn.executemany(
                    """INSERT INTO canonical_events VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    (
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
                        for e in canonical
                    ),
                )
                ins = self.conn.total_changes - c_before
                if ins < len(canonical):
                    self.conflicts += len(canonical) - ins
            if quarantine:
                self.conn.executemany(
                    """INSERT INTO quarantine VALUES
                       (?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    quarantine,
                )
                self._append_quarantine_merkle_batch_in_tx(quarantine)
            if lineage:
                self.conn.executemany(
                    """INSERT INTO lineage VALUES
                       (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    lineage,
                )
            if source_health:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?,?)",
                    source_health,
                )

    @_synchronized
    def commit(self):
        """Commit active database transaction."""
        self.conn.commit()

    # -- Rowid cursor tailing for streaming sinks (Kafka, downstream consumers) --

    @_read_synchronized
    def get_max_rowid(self, table: str = "canonical_events") -> int:
        """Return the maximum rowid currently stored in a table."""
        # Clean table name to prevent SQL injection
        tbl_clean = (
            "canonical_events"
            if table == "canonical_events"
            else ("quarantine" if table == "quarantine" else "canonical_events")
        )
        cur = self.read_conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {tbl_clean}")
        row = cur.fetchone()
        return row[0] if row else 0

    @_read_synchronized
    def query_canonical_after_rowid(
        self, last_rowid: int, limit: int = 100
    ) -> list[tuple]:
        """Fetch canonical events with rowid strictly greater than last_rowid in monotonic order."""
        limit = min(max(1, limit), 10000)
        cur = self.read_conn.execute(
            """SELECT rowid, event_id, instrument_id, event_type, exchange_timestamp,
                      receive_timestamp, processing_timestamp, source, sequence_number,
                      price, quantity, bid_price, bid_size, ask_price, ask_size,
                      quality_status, reasons, raw_id
               FROM canonical_events
               WHERE rowid > ?
               ORDER BY rowid ASC
               LIMIT ?""",
            (last_rowid, limit),
        )
        return cur.fetchall()

    @_read_synchronized
    def query_quarantine_after_rowid(
        self, last_rowid: int, limit: int = 100
    ) -> list[tuple]:
        """Fetch quarantine records with rowid strictly greater than last_rowid in monotonic order."""
        limit = min(max(1, limit), 10000)
        cur = self.read_conn.execute(
            """SELECT rowid, event_id, instrument_id, source, quality_status, reasons,
                      payload_json, receive_timestamp
               FROM quarantine
               WHERE rowid > ?
               ORDER BY rowid ASC
               LIMIT ?""",
            (last_rowid, limit),
        )
        return cur.fetchall()

    # -- Query helpers (backs the CLI `query` subcommand / future API) --

    @_read_synchronized
    def latest(self, instrument_id: str, limit: int = 1):
        limit = min(max(1, limit), 10000)
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
        limit = min(max(1, limit), 10000)
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
        limit = min(max(1, limit), 10000)
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
        """Delete canonical_events older than retain_days in chunks, then reclaim disk.

        quarantine records are kept for quarantine_days (default 90) for
        evidentiary/audit compliance, while operational events prune at retain_days.
        Does NOT touch audit_log or lineage (append-only / WORM by design, spec A3).
        Returns dict with counts of deleted rows and compaction status.
        """
        cutoff = time.time() - (retain_days * 86400)
        q_cutoff = time.time() - (quarantine_days * 86400)
        deleted_canonical = 0
        deleted_quarantine = 0

        while True:
            cur = self.conn.execute(
                "DELETE FROM canonical_events WHERE rowid IN ("
                "SELECT rowid FROM canonical_events WHERE COALESCE(processing_timestamp, exchange_timestamp) < ? LIMIT 5000"
                ")",
                (cutoff,),
            )
            deleted_canonical += cur.rowcount
            self.conn.commit()
            if cur.rowcount < 5000:
                break

        while True:
            cur_q = self.conn.execute(
                "DELETE FROM quarantine WHERE rowid IN ("
                "SELECT rowid FROM quarantine WHERE receive_timestamp < ? LIMIT 5000"
                ")",
                (q_cutoff,),
            )
            deleted_quarantine += cur_q.rowcount
            self.conn.commit()
            if cur_q.rowcount < 5000:
                break

        try:
            self.conn.execute("DELETE FROM vwap_curves WHERE timestamp < ?", (cutoff,))
            self.conn.execute(
                "DELETE FROM watchdog_alerts WHERE timestamp < ?", (cutoff,)
            )
            self.conn.commit()
        except Exception:
            pass

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
        format_version: int = 2,
    ) -> None:
        self.conn.execute(
            """INSERT INTO audit_log (timestamp, actor, role, action, details, prev_hash, entry_hash, format_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                actor,
                role,
                action,
                details,
                prev_hash,
                entry_hash,
                format_version,
            ),
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
        limit = min(max(1, limit), 10000)
        cur = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY entry_id DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_synchronized
    def append_audit(
        self,
        actor: str,
        role: str,
        action: str,
        details: str,
        timestamp: float | None = None,
        format_version: int = 2,
    ) -> str:
        """Atomic audit log append: reads head, computes hash, inserts, and commits in one transaction."""
        from audit_format import compute_audit_hash

        ts = timestamp if timestamp is not None else time.time()
        with self.transaction():
            cur = self.conn.execute(
                "SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1"
            )
            row = cur.fetchone()
            prev_hash = (
                row[0]
                if row
                else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            )
            entry_hash = compute_audit_hash(
                prev_hash,
                ts,
                actor,
                role,
                action,
                details,
                format_version=format_version,
            )
            self.conn.execute(
                """INSERT INTO audit_log (timestamp, actor, role, action, details, prev_hash, entry_hash, format_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    actor,
                    role,
                    action,
                    details,
                    prev_hash,
                    entry_hash,
                    format_version,
                ),
            )
        return entry_hash

    def _append_quarantine_merkle_batch_in_tx(
        self, quarantine_rows: list[tuple], timestamp: float | None = None
    ) -> str | None:
        """Internal helper to insert a Merkle root for a quarantine batch within an existing transaction."""
        if not quarantine_rows:
            return None
        from audit_format import compute_audit_hash

        ts = timestamp if timestamp is not None else time.time()
        leaves = [
            hashlib.sha256(
                json.dumps(row, default=str, sort_keys=True).encode("utf-8")
            ).digest()
            for row in quarantine_rows
        ]
        batch_root = _compute_merkle_root(leaves)
        cur = self.conn.execute(
            "SELECT entry_hash FROM quarantine_merkle_log ORDER BY entry_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        prev_root = (
            row[0]
            if row
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        details = f"n={len(quarantine_rows)},root={batch_root}"
        entry_hash = compute_audit_hash(
            prev_root,
            ts,
            "system",
            "pipeline",
            "QUARANTINE_BATCH",
            details,
            format_version=3,
        )
        self.conn.execute(
            """INSERT INTO quarantine_merkle_log
               (timestamp, entry_hash, prev_root, batch_root, batch_size, format_version)
               VALUES (?, ?, ?, ?, ?, 3)""",
            (ts, entry_hash, prev_root, batch_root, len(quarantine_rows)),
        )
        return entry_hash

    @_synchronized
    def append_quarantine_merkle_batch(
        self, quarantine_rows: list[tuple], timestamp: float | None = None
    ) -> str | None:
        """Append a batched Merkle root record for quarantined records."""
        with self.transaction():
            return self._append_quarantine_merkle_batch_in_tx(
                quarantine_rows, timestamp
            )

    @_synchronized
    def verify_quarantine_merkle_integrity(self) -> tuple[bool, str, int]:
        """Verify the cryptographic hash-chain and batch Merkle roots of the quarantine log."""
        from audit_format import compute_audit_hash

        cur = self.conn.execute(
            "SELECT entry_id, timestamp, entry_hash, prev_root, batch_root, batch_size, format_version "
            "FROM quarantine_merkle_log ORDER BY entry_id ASC"
        )
        rows = cur.fetchall()
        if not rows:
            return True, "Quarantine Merkle log is empty (valid)", 0

        expected_prev = (
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        for entry_id, ts, entry_hash, prev_root, batch_root, batch_size, f_ver in rows:
            if prev_root != expected_prev:
                return (
                    False,
                    f"Broken Merkle chain link at entry #{entry_id}: expected prev_root '{expected_prev[:12]}...', got '{prev_root[:12]}...'",
                    entry_id,
                )
            details = f"n={batch_size},root={batch_root}"
            recomputed = compute_audit_hash(
                prev_root,
                ts,
                "system",
                "pipeline",
                "QUARANTINE_BATCH",
                details,
                format_version=f_ver or 3,
            )
            if recomputed != entry_hash:
                return (
                    False,
                    f"Tampered quarantine Merkle entry #{entry_id}: hash mismatch",
                    entry_id,
                )
            expected_prev = entry_hash

        return (
            True,
            f"Quarantine Merkle log verified ({len(rows)} batches intact)",
            len(rows),
        )

    @_synchronized
    def verify_audit_integrity(
        self, anchor: tuple[int, str] | None = None
    ) -> tuple[bool, str, int]:
        from audit_format import compute_audit_hash

        cur = self.conn.execute(
            "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash, "
            "COALESCE(format_version, 1) FROM audit_log ORDER BY entry_id ASC"
        )
        rows = cur.fetchall()
        if not rows:
            if anchor is not None and (
                anchor[0] != 0
                or anchor[1]
                != "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
            ):
                return False, f"Audit log is wiped: expected {anchor[0]} entries", 0
            return True, "Audit log is empty (valid)", 0

        if anchor is not None:
            expected_count, expected_head = anchor
            if len(rows) != expected_count:
                return (
                    False,
                    f"Tail truncation detected: expected {expected_count} entries, got {len(rows)}",
                    len(rows),
                )
            if rows[-1][7] != expected_head:
                return (
                    False,
                    f"Chain head mismatch: expected '{expected_head[:12]}...', got '{rows[-1][7][:12]}...'",
                    len(rows),
                )

        expected_prev = (
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        for entry_id, ts, actor, role, action, details, prev_h, entry_h, f_ver in rows:
            if prev_h != expected_prev:
                return (
                    False,
                    f"Broken chain link at entry #{entry_id}: expected prev_hash '{expected_prev[:12]}...', got '{prev_h[:12]}...'",
                    entry_id,
                )
            recomputed_hash = compute_audit_hash(
                prev_h, ts, actor, role, action, details, format_version=f_ver
            )
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
            "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash, "
            "COALESCE(format_version, 1) FROM audit_log ORDER BY entry_id ASC"
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
            "format_version",
        ]
        entries = [dict(zip(cols, r)) for r in cur.fetchall()]

        latest_hash = (
            entries[-1]["entry_hash"]
            if entries
            else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        proof = {
            "version": "2.0.0",
            "specification": "MDRAP-Spec-19.3",
            "algorithm": "sha256",
            "genesis_hash": "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
            "total_entries": len(entries),
            "latest_hash": latest_hash,
            "entries": entries,
        }
        if output_file:
            dir_name = os.path.dirname(os.path.abspath(output_file))
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            tmp_file = f"{output_file}.tmp.{os.getpid()}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(proof, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, output_file)
        return proof

    @staticmethod
    def verify_standalone_proof(
        proof_data_or_path: Any, anchor: tuple[int, str] | None = None
    ) -> tuple[bool, str, int]:
        """Independently verify a JSON audit proof without database access."""
        import json
        from audit_format import compute_audit_hash

        if isinstance(proof_data_or_path, str):
            with open(proof_data_or_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = proof_data_or_path

        entries = data.get("entries", [])
        genesis_hash = data.get(
            "genesis_hash",
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
        )
        expected_latest = entries[-1]["entry_hash"] if entries else genesis_hash
        declared_latest = data.get("latest_hash")
        declared_total = data.get("total_entries")

        if declared_latest is not None and declared_latest != expected_latest:
            return (
                False,
                f"Latest hash mismatch: declared '{declared_latest}', calculated '{expected_latest}'",
                0,
            )
        if declared_total is not None and declared_total != len(entries):
            return (
                False,
                f"Total entries mismatch: declared {declared_total}, actual {len(entries)}",
                0,
            )

        if not entries:
            if anchor is not None and anchor[0] == 0:
                return True, "Audit proof is empty (valid by anchor)", 0
            return False, "Audit proof is empty (invalid without confirmed anchor)", 0

        if anchor is not None:
            expected_count, expected_head = anchor
            if len(entries) != expected_count:
                return (
                    False,
                    f"Anchor count mismatch: expected {expected_count}, got {len(entries)}",
                    len(entries),
                )
            if entries[-1]["entry_hash"] != expected_head:
                return (
                    False,
                    f"Anchor head hash mismatch: expected '{expected_head[:12]}...', got '{entries[-1]['entry_hash'][:12]}...'",
                    len(entries),
                )

        expected_prev = genesis_hash
        for item in entries:
            entry_id = item["entry_id"]
            prev_h = item["prev_hash"]
            entry_h = item["entry_hash"]
            f_ver = item.get("format_version", 1)

            if prev_h != expected_prev:
                return (
                    False,
                    f"Broken chain link at entry #{entry_id}: expected prev '{expected_prev[:12]}...', got '{prev_h[:12]}...'",
                    entry_id,
                )

            calc_hash = compute_audit_hash(
                prev_h,
                item["timestamp"],
                item["actor"],
                item["role"],
                item["action"],
                item["details"],
                format_version=f_ver,
            )
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
        """Save or update a client API key (stores token_hash, never raw token)."""
        role_val = getattr(ent, "role", "VIEWER")
        role_str = role_val.value if hasattr(role_val, "value") else str(role_val)

        raw_token = getattr(ent, "token", "")
        token_hash = getattr(ent, "token_hash", "")
        if not token_hash and raw_token:
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            ent.token_hash = token_hash
        if not token_hash:
            raise ValueError("Cannot persist API key without token or token_hash")

        key_prefix = getattr(ent, "key_prefix", "")
        if not key_prefix and raw_token:
            key_prefix = raw_token[:12] + "..." if len(raw_token) > 12 else raw_token
            ent.key_prefix = key_prefix
        elif not key_prefix:
            key_prefix = token_hash[:12] + "..."
            ent.key_prefix = key_prefix

        cur_cols = {
            c[1] for c in self.conn.execute("PRAGMA table_info(api_keys)").fetchall()
        }
        if "rate_limit_eps" in cur_cols:
            self.conn.execute(
                """INSERT OR REPLACE INTO api_keys
                   (token_hash, key_prefix, client_id, role, rate_limit_eps, tier, can_access_l2, can_use_binary, can_use_shm, max_replay_events, is_active, created_at, expires_at)
                   VALUES (?, ?, ?, ?, 20000.0, 'STANDARD', 1, 1, 1, 100000, ?, ?, ?)""",
                (
                    token_hash,
                    key_prefix,
                    ent.client_id,
                    role_str,
                    1 if ent.is_active else 0,
                    float(ent.created_at),
                    float(ent.expires_at) if ent.expires_at is not None else None,
                ),
            )
        else:
            self.conn.execute(
                """INSERT OR REPLACE INTO api_keys
                   (token_hash, key_prefix, client_id, role, is_active, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_hash,
                    key_prefix,
                    ent.client_id,
                    role_str,
                    1 if ent.is_active else 0,
                    float(ent.created_at),
                    float(ent.expires_at) if ent.expires_at is not None else None,
                ),
            )
        self.conn.commit()

    @_synchronized
    def load_api_keys(self) -> list:
        """Load all registered API keys from the store."""
        from security import ClientEntitlement, Role

        cur = self.conn.execute(
            """SELECT token_hash, key_prefix, client_id, role, is_active, created_at, expires_at
               FROM api_keys"""
        )
        results = []
        for row in cur.fetchall():
            role_raw = row[3]
            role = Role[role_raw] if role_raw in Role.__members__ else Role.VIEWER
            results.append(
                ClientEntitlement(
                    token_hash=row[0],
                    key_prefix=row[1],
                    token=row[
                        0
                    ],  # for backward compatibility where ent.token is used in tests/maps
                    client_id=row[2],
                    role=role,
                    is_active=bool(row[4]),
                    created_at=float(row[5]),
                    expires_at=float(row[6]) if row[6] is not None else None,
                )
            )
        return results

    @_synchronized
    def revoke_api_key(self, token_or_hash: str) -> bool:
        """Mark an API key as inactive by token, token_hash, or key_prefix."""
        tok_hash = hashlib.sha256(token_or_hash.encode("utf-8")).hexdigest()
        cur = self.conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE token_hash = ? OR token_hash = ? OR key_prefix = ?",
            (tok_hash, token_or_hash, token_or_hash),
        )
        self.conn.commit()
        return cur.rowcount > 0
