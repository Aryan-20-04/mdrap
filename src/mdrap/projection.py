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
                CREATE TABLE IF NOT EXISTS quarantine_events (
                    event_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    quality_status TEXT NOT NULL,
                    reasons TEXT,
                    raw_payload TEXT,
                    created_at REAL NOT NULL
                );
                """
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
                        INSERT OR IGNORE INTO quarantine_events (
                            event_id, instrument_id, source,
                            quality_status, reasons, raw_payload, created_at
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
