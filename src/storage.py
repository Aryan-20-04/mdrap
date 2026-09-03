"""
Storage layer (MVP): SQLite, batched writes.

Separates raw/processed/derived data into distinct tables per the
spec's "never conflate raw and derived" principle:
  - canonical_events : VALID + SUSPICIOUS events (the hot/queryable stream)
  - quarantine        : SUSPICIOUS + INVALID events, kept for inspection/replay
  - lineage            : one row per canonical decision, traceable back to source
  - source_health      : latest reliability snapshot per source

SQLite is the pragmatic MVP choice -- section 5 of the spec names
PostgreSQL/ClickHouse as the scale-up path once a baseline exists.
Writes are batched (executemany) because per-row commits are the
dominant cost at high event rates; this is exactly the kind of thing
the benchmark harness should measure and justify.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Iterable, List, Optional

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
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        self.conn.execute("PRAGMA busy_timeout=5000;")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            pass  # in-memory or read-only filesystems do not support WAL
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def write_canonical_batch(self, events: List[CanonicalEvent]):
        if not events:
            return
        self.conn.executemany(
            """INSERT OR REPLACE INTO canonical_events VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(e.event_id, e.instrument_id, e.event_type.value, e.exchange_timestamp,
              e.receive_timestamp, e.processing_timestamp, e.source, e.sequence_number,
              e.price, e.quantity, e.bid_price, e.bid_size, e.ask_price, e.ask_size,
              e.quality_status.value, json.dumps(e.reasons), e.raw_id) for e in events],
        )

    def write_quarantine_batch(self, rows: List[tuple]):
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO quarantine VALUES (?,?,?,?,?,?,?)", rows,
        )

    def write_lineage_batch(self, rows: List[tuple]):
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO lineage VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows,
        )

    def upsert_source_health(self, rows: List[tuple]):
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?,?)", rows,
        )

    def commit(self):
        self.conn.commit()

    # -- Query helpers (backs the CLI `query` subcommand / future API) --

    def latest(self, instrument_id: str, limit: int = 1):
        cur = self.conn.execute(
            """SELECT * FROM canonical_events WHERE instrument_id=?
               ORDER BY exchange_timestamp DESC LIMIT ?""",
            (instrument_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def event_lineage(self, event_id: str) -> Optional[dict]:
        cur = self.conn.execute("SELECT * FROM lineage WHERE event_id=?", (event_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def feed_health(self):
        cur = self.conn.execute("SELECT * FROM source_health")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def quarantine_sample(self, limit: int = 20):
        cur = self.conn.execute("SELECT * FROM quarantine LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_quarantine(self, limit: int = 50):
        return self.quarantine_sample(limit)

    def counts(self):
        cur = self.conn.execute(
            "SELECT quality_status, COUNT(*) FROM canonical_events GROUP BY quality_status")
        return dict(cur.fetchall())

    def close(self):
        self.conn.commit()
        self.conn.close()

    # -- V3 Analytical write methods --

    def write_ohlcv_batch(self, candles: list[dict]) -> None:
        if not candles:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO ohlcv_candles VALUES (?,?,?,?,?,?,?,?,?)",
            [(c["instrument_id"], c["bucket_start"], c["interval_s"],
              c["open"], c["high"], c["low"], c["close"],
              c["volume"], c["event_count"]) for c in candles],
        )

    def write_spread_batch(self, spreads: list[dict]) -> None:
        if not spreads:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO spread_stats VALUES (?,?,?,?,?,?,?)",
            [(s["instrument_id"], s["quote_count"], s["mean_spread"],
              s["min_spread"], s["max_spread"], s["crossed_count"],
              s["crossed_pct"]) for s in spreads],
        )

    def write_volatility_batch(self, stats: list[dict]) -> None:
        if not stats:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO volatility_stats VALUES (?,?,?,?,?,?,?)",
            [(v["instrument_id"], v["trade_count"], v["mean_price"],
              v["std_dev"], v["min_price"], v["max_price"],
              v["price_range_pct"]) for v in stats],
        )

    # -- V3 Analytical query methods --

    def query_ohlcv(self, instrument_id: str = None, limit: int = 50) -> list[dict]:
        if instrument_id:
            cur = self.conn.execute(
                "SELECT * FROM ohlcv_candles WHERE instrument_id=? ORDER BY bucket_start DESC LIMIT ?",
                (instrument_id, limit))
        else:
            cur = self.conn.execute(
                "SELECT * FROM ohlcv_candles ORDER BY instrument_id, bucket_start DESC LIMIT ?",
                (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_spread(self, instrument_id: str = None) -> list[dict]:
        if instrument_id:
            cur = self.conn.execute("SELECT * FROM spread_stats WHERE instrument_id=?", (instrument_id,))
        else:
            cur = self.conn.execute("SELECT * FROM spread_stats")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_volatility(self, instrument_id: str = None) -> list[dict]:
        if instrument_id:
            cur = self.conn.execute("SELECT * FROM volatility_stats WHERE instrument_id=?", (instrument_id,))
        else:
            cur = self.conn.execute("SELECT * FROM volatility_stats")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Phase 7 Watchdog alert methods --

    def write_alert_batch(self, alerts) -> None:
        if not alerts:
            return
        self.conn.executemany(
            "INSERT INTO watchdog_alerts (source, alert_type, timestamp, details, action_taken) VALUES (?,?,?,?,?)",
            [(a.source, a.alert_type, a.timestamp, a.details, a.action_taken) for a in alerts],
        )

    def query_alerts(self, limit: int = 20) -> list[dict]:
        cur = self.conn.execute(
            "SELECT source, alert_type, timestamp, details, action_taken FROM watchdog_alerts ORDER BY rowid DESC LIMIT ?",
            (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Consolidated BBO methods --

    def write_bbo_batch(self, bbos: list) -> None:
        if not bbos:
            return
        now = time.time()
        rows = []
        for b in bbos:
            if hasattr(b, "instrument_id"):
                rows.append((
                    b.instrument_id, b.best_bid, b.best_bid_size, b.best_bid_source,
                    b.best_ask, b.best_ask_size, b.best_ask_source,
                    b.spread, b.mid_price, 1 if b.is_crossed else 0,
                    1 if b.is_locked else 0, b.timestamp, now
                ))
            elif isinstance(b, dict):
                rows.append((
                    b["instrument_id"], b["best_bid"], b["best_bid_size"], b["best_bid_source"],
                    b["best_ask"], b["best_ask_size"], b["best_ask_source"],
                    b["spread"], b["mid_price"], 1 if b.get("is_crossed") else 0,
                    1 if b.get("is_locked") else 0, b["timestamp"], now
                ))
        self.conn.executemany(
            """INSERT OR REPLACE INTO consolidated_bbo VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )

    def query_bbo(self, instrument_id: Optional[str] = None) -> list[dict]:
        if instrument_id:
            cur = self.conn.execute("SELECT * FROM consolidated_bbo WHERE instrument_id=?", (instrument_id,))
        else:
            cur = self.conn.execute("SELECT * FROM consolidated_bbo ORDER BY instrument_id ASC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def write_audit_entry(self, timestamp: float, actor: str, role: str, action: str,
                          details: str, prev_hash: str, entry_hash: str) -> None:
        self.conn.execute(
            """INSERT INTO audit_log (timestamp, actor, role, action, details, prev_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, actor, role, action, details, prev_hash, entry_hash),
        )

    def get_latest_audit_hash(self) -> str:
        cur = self.conn.execute("SELECT entry_hash FROM audit_log ORDER BY entry_id DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"

    def query_audit_log(self, limit: int = 50) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM audit_log ORDER BY entry_id DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def verify_audit_integrity(self) -> tuple[bool, str, int]:
        import hashlib
        cur = self.conn.execute(
            "SELECT entry_id, timestamp, actor, role, action, details, prev_hash, entry_hash FROM audit_log ORDER BY entry_id ASC"
        )
        rows = cur.fetchall()
        if not rows:
            return True, "Audit log is empty (valid)", 0

        expected_prev = "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        for entry_id, ts, actor, role, action, details, prev_h, entry_h in rows:
            if prev_h != expected_prev:
                return False, f"Broken chain link at entry #{entry_id}: expected prev_hash '{expected_prev[:12]}...', got '{prev_h[:12]}...'", entry_id
            payload_str = f"{prev_h}|{ts:.6f}|{actor}|{role}|{action}|{details}"
            recomputed_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            if recomputed_hash != entry_h:
                return False, f"Tampered entry #{entry_id}: hash mismatch (stored '{entry_h[:12]}...', calculated '{recomputed_hash[:12]}...')", entry_id
            expected_prev = entry_h

        return True, f"Cryptographic audit chain verified ({len(rows)} entries intact)", len(rows)


