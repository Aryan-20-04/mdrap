"""Storage schema, persistence modes, constants, and synchronization wrappers.

Part of MDRAP Storage Layer decomposition (Spec §5 & §26).
"""

from __future__ import annotations

from enum import Enum
import functools
import hashlib
import sqlite3
import threading
import time
from typing import Any

__stability__ = "stable"


class PersistenceMode(str, Enum):
    """Explicit persistence modes governed by operational requirements (Spec §5 & Task 1)."""

    PRODUCTION_DURABLE = "production_durable"
    DEVELOPMENT_IN_MEMORY = "development_in_memory"
    DEGRADED = "degraded"


class StorageConflictError(RuntimeError):
    """Raised when database inserts suffer unhandled primary key conflicts."""

    pass


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
CREATE VIEW IF NOT EXISTS quarantine_events AS SELECT * FROM quarantine;

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    name TEXT PRIMARY KEY,
    last_offset INTEGER NOT NULL,
    updated_at REAL NOT NULL
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
    entry_hash TEXT,
    format_version INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);

CREATE TABLE IF NOT EXISTS api_keys (
    token_hash TEXT PRIMARY KEY,
    key_prefix TEXT NOT NULL DEFAULT '',
    key_id TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'VIEWER',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    expires_at REAL,
    allowed_cidrs TEXT NOT NULL DEFAULT '[]',
    allowed_sources TEXT NOT NULL DEFAULT '[]',
    allowed_symbols TEXT NOT NULL DEFAULT '[]'
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

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL,
    description TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Storage Tier & SQLite Concurrency Tuning Constants (Spec §5 & §26)
# ---------------------------------------------------------------------------
DEFAULT_SQLITE_RETRIES: int = 3
SQLITE_RETRY_BACKOFF_WRITE_S: float = 0.05  # 50 ms backoff on write lock contention
SQLITE_RETRY_BACKOFF_READ_S: float = 0.01  # 10 ms backoff on read contention
DEFAULT_SQLITE_TIMEOUT_S: float = 30.0  # 30-second busy timeout
DEFAULT_BUSY_TIMEOUT_MS: int = 30000  # 30,000 ms pragma busy timeout
DEFAULT_SQLITE_MMAP_MB: int = 256  # 256 MB mmap
DEFAULT_SQLITE_CACHE_MB: int = 64  # 64 MB dedicated cache
BYTES_PER_MB: int = 1024 * 1024
PAGE_CACHE_KIB_PER_MB: int = 1000
CURRENT_SCHEMA_VERSION: int = 3


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

    @functools.wraps(method)
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

    return wrapper


def _read_synchronized(method):
    """Thread-safe synchronization wrapper for read queries using decoupled read_conn."""

    @functools.wraps(method)
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

    return wrapper
