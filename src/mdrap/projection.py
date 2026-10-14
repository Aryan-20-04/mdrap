"""Atomic SQLite Projection for MDRAP.

Implements the Projection interface:
    apply(batch, offset)
    checkpoint()

Guarantees atomic commit of (rows + checkpoint) in a single SQLite transaction,
providing conflict-free, idempotent replay and crash recovery.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Protocol

from .models import CanonicalEvent, QualityStatus
from .engine import EngineDecision

__stability__ = "stable"


logger = logging.getLogger(__name__)


class Projection(Protocol):
    """Protocol defining consumer projections over the deterministic engine stream."""

    def apply(self, batch: list[Any], offset: int) -> None:
        """Atomically apply a batch of engine decisions/events and advance the checkpoint to offset."""
        ...

    def checkpoint(self) -> int:
        """Return the highest durably committed log offset, or -1 if no checkpoint exists."""
        ...


class SQLiteProjection:
    """Production SQLite projection with atomic row + checkpoint transaction commit."""

    def __init__(self, db_path: str, name: str = "sqlite_canonical") -> None:
        self.db_path = db_path
        self.name = name
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # Explicit transaction management
        )
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute("PRAGMA busy_timeout = 10000;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS projection_checkpoints (
                    name TEXT PRIMARY KEY,
                    last_offset INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS canonical_events (
                    event_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    exchange_timestamp REAL NOT NULL,
                    receive_timestamp REAL NOT NULL,
                    processing_timestamp REAL NOT NULL,
                    source TEXT NOT NULL,
                    sequence_number INTEGER,
                    price REAL,
                    quantity REAL,
                    bid_price REAL,
                    ask_price REAL,
                    bid_size REAL,
                    ask_size REAL,
                    quality_status TEXT NOT NULL,
                    reasons TEXT,
                    raw_id TEXT
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantine (
                    event_id TEXT PRIMARY KEY,
                    instrument_id TEXT,
                    source TEXT,
                    quality_status TEXT,
                    reasons TEXT,
                    payload_json TEXT,
                    receive_timestamp REAL
                );
                """
            )
            cur.execute(
                "CREATE VIEW IF NOT EXISTS quarantine_events AS SELECT * FROM quarantine;"
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_canonical_inst_ts 
                ON canonical_events(instrument_id, exchange_timestamp);
                """
            )

    def checkpoint(self) -> int:
        """Return the latest committed offset, or -1 if no checkpoint exists."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT last_offset FROM projection_checkpoints WHERE name = ?;",
                (self.name,),
            )
            row = cur.fetchone()
            return row[0] if row else -1

    def apply(self, batch: list[Any], offset: int) -> None:
        """Atomically persist rows and advance checkpoint in a single SQLite transaction."""
        if not batch and offset < 0:
            return

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")

                canonical_rows: list[tuple] = []
                quarantine_rows: list[tuple] = []

                for item in batch:
                    if isinstance(item, EngineDecision):
                        if item.canonical_event and item.quality_status != QualityStatus.INVALID:
                            ev = item.canonical_event
                            canonical_rows.append(
                                (
                                    ev.event_id,
                                    ev.instrument_id,
                                    ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type),
                                    ev.exchange_timestamp,
                                    ev.receive_timestamp,
                                    ev.processing_timestamp,
                                    ev.source,
                                    ev.sequence_number,
                                    ev.price,
                                    ev.quantity,
                                    ev.bid_price,
                                    ev.ask_price,
                                    ev.bid_size,
                                    ev.ask_size,
                                    ev.quality_status.value if hasattr(ev.quality_status, "value") else str(ev.quality_status),
                                    json.dumps(ev.reasons),
                                    str(ev.raw_id) if ev.raw_id is not None else None,
                                )
                            )
                        if item.quarantine_row:
                            quarantine_rows.append(item.quarantine_row)
                    elif isinstance(item, CanonicalEvent):
                        if item.quality_status != QualityStatus.INVALID:
                            canonical_rows.append(
                                (
                                    item.event_id,
                                    item.instrument_id,
                                    item.event_type.value if hasattr(item.event_type, "value") else str(item.event_type),
                                item.exchange_timestamp,
                                item.receive_timestamp,
                                item.processing_timestamp,
                                item.source,
                                item.sequence_number,
                                item.price,
                                item.quantity,
                                item.bid_price,
                                item.ask_price,
                                item.bid_size,
                                item.ask_size,
                                item.quality_status.value if hasattr(item.quality_status, "value") else str(item.quality_status),
                                json.dumps(item.reasons),
                                str(item.raw_id) if item.raw_id is not None else None,
                            )
                        )
                        else:
                            q_row = (
                                item.event_id,
                                item.instrument_id,
                                item.source,
                                QualityStatus.INVALID.value,
                                json.dumps(item.reasons),
                                json.dumps({"raw_id": item.raw_id}),
                                item.receive_timestamp,
                            )
                            quarantine_rows.append(q_row)
                    elif isinstance(item, tuple):
                        # Assume quarantine or canonical row based on length
                        if len(item) == 7:
                            quarantine_rows.append(item)

                if canonical_rows:
                    cur.executemany(
                        """
                        INSERT OR IGNORE INTO canonical_events (
                            event_id, instrument_id, event_type,
                            exchange_timestamp, receive_timestamp, processing_timestamp,
                            source, sequence_number, price, quantity,
                            bid_price, ask_price, bid_size, ask_size,
                            quality_status, reasons, raw_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        canonical_rows,
                    )

                if quarantine_rows:
                    cur.executemany(
                        """
                        INSERT OR IGNORE INTO quarantine (
                            event_id, instrument_id, source,
                            quality_status, reasons, payload_json, receive_timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        quarantine_rows,
                    )

                # Atomically update checkpoint in the SAME transaction!
                cur.execute(
                    """
                    INSERT OR REPLACE INTO projection_checkpoints (name, last_offset, updated_at)
                    VALUES (?, ?, ?);
                    """,
                    (self.name, offset, time.time()),
                )

                cur.execute("COMMIT;")
            except Exception:
                cur.execute("ROLLBACK;")
                raise

    def count_canonical(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM canonical_events;")
            return cur.fetchone()[0]

    def count_quarantine(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM quarantine_events;")
            return cur.fetchone()[0]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


class DuckDBProjection:
    """Production DuckDB columnar projection for ultra-fast time-series analytical scans."""

    def __init__(self, db_path: str = ":memory:", name: str = "duckdb_canonical") -> None:
        try:
            import duckdb
        except ImportError:
            raise RuntimeError("duckdb is not installed; install via pip install duckdb")
        self.db_path = db_path
        self.name = name
        self._lock = threading.Lock()
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projection_checkpoints (
                    name VARCHAR PRIMARY KEY,
                    last_offset BIGINT NOT NULL,
                    updated_at DOUBLE NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS canonical_events (
                    event_id VARCHAR PRIMARY KEY,
                    instrument_id VARCHAR NOT NULL,
                    event_type VARCHAR NOT NULL,
                    exchange_timestamp DOUBLE NOT NULL,
                    receive_timestamp DOUBLE NOT NULL,
                    processing_timestamp DOUBLE NOT NULL,
                    source VARCHAR NOT NULL,
                    sequence_number BIGINT,
                    price DOUBLE,
                    quantity DOUBLE,
                    bid_price DOUBLE,
                    ask_price DOUBLE,
                    bid_size DOUBLE,
                    ask_size DOUBLE,
                    quality_status VARCHAR NOT NULL,
                    reasons VARCHAR,
                    raw_id VARCHAR
                );
                """
            )

    def checkpoint(self) -> int:
        with self._lock:
            res = self._conn.execute(
                "SELECT last_offset FROM projection_checkpoints WHERE name = ?",
                [self.name],
            ).fetchone()
            return int(res[0]) if res else -1

    def apply(self, batch: list[Any], offset: int) -> None:
        if not batch:
            return
        canonical_rows = []
        for item in batch:
            ev = getattr(item, "canonical_event", None)
            if ev is not None:
                r_json = json.dumps(ev.reasons) if isinstance(ev.reasons, list) else str(ev.reasons or "")
                ev_type_str = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
                canonical_rows.append((
                    ev.event_id,
                    ev.instrument_id,
                    ev_type_str,
                    float(ev.exchange_timestamp or 0.0),
                    float(ev.receive_timestamp or 0.0),
                    float(ev.processing_timestamp or 0.0),
                    ev.source,
                    ev.sequence_number,
                    ev.price,
                    ev.quantity,
                    ev.bid_price,
                    ev.ask_price,
                    ev.bid_size,
                    ev.ask_size,
                    ev.quality_status.value if hasattr(ev.quality_status, "value") else str(ev.quality_status),
                    r_json,
                    ev.raw_id,
                ))
        with self._lock:
            if canonical_rows:
                self._conn.executemany(
                    """
                    INSERT OR IGNORE INTO canonical_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    canonical_rows,
                )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO projection_checkpoints VALUES (?, ?, ?);
                """,
                [self.name, offset, time.time()],
            )

    def count_canonical(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0])

    def query(self, sql: str, params: list | None = None) -> list:
        with self._lock:
            return self._conn.execute(sql, params or []).fetchall()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
