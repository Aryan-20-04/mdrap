"""Atomic SQLite and Downstream Projections for MDRAP.

Implements the Projection interface:
    apply(batch, offset)
    checkpoint()

Architectural Invariant:
    The IngestLog WAL is the authoritative, immutable source of truth.
    Downstream SQLite, DuckDB, and Parquet projections are rebuildable,
    materialized views. When projections lag, fail, or need schema changes,
    they can be deterministically reconstructed from the WAL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import queue
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


@dataclass
class ProjectionTracker:
    """Coordinates delivery, bounded retries, and contiguous checkpoint tracking for a projection."""

    projection: Any
    name: str = "projection"
    last_contiguous_offset: int = -1
    failed_offsets: list[int] = field(default_factory=list)
    pending_batches: list[tuple[int, list[Any]]] = field(default_factory=list)
    consecutive_failures: int = 0
    total_retries: int = 0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    recovery_status: str = "IDLE"  # "IDLE", "RECOVERING", "DEGRADED", "STALLED"
    max_pending: int = 50_000
    queue_overflow_count: int = 0
    wal_catchup_needed: bool = False
    max_enqueued_wal_offset: int = -1
    log_source: Any | None = None
    replay_fn: Any | None = None
    async_drain: bool = False
    batch_size: int = 1000
    flush_interval_sec: float = 0.010
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _queue_lock: threading.Lock = field(default_factory=threading.Lock)
    _wake_event: threading.Event = field(default_factory=threading.Event)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _worker_thread: threading.Thread | None = None

    def __post_init__(self) -> None:
        if hasattr(self.projection, "checkpoint"):
            try:
                cp = self.projection.checkpoint()
                if cp >= 0:
                    self.last_contiguous_offset = cp
            except Exception:
                pass
        if self.async_drain:
            self.start_worker()

    def start_worker(self) -> None:
        """Start background projection drainage worker thread."""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"mdrap-proj-{self.name}",
            )
            self._worker_thread.start()
            self.async_drain = True

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self.flush_interval_sec)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            with self._queue_lock:
                has_work = bool(self.pending_batches or self.wal_catchup_needed)
            if has_work:
                self._drain_batches(timeout=0.005)
        # Final drain on worker termination
        self._drain_batches(timeout=0.5)

    def enqueue(
        self,
        batch: list[Any],
        offset: int,
        batch_start: int | None = None,
    ) -> None:
        """Enqueue batch into ordered reordering buffer without disk I/O."""
        if not batch and offset < 0:
            return
        if offset >= 0:
            self.max_enqueued_wal_offset = max(self.max_enqueued_wal_offset, offset)

        with self._queue_lock:
            if len(self.pending_batches) >= self.max_pending:
                self.queue_overflow_count += 1
                self.recovery_status = "DEGRADED"
                self.wal_catchup_needed = True
                return

            if not self.pending_batches or offset >= self.pending_batches[-1][0]:
                self.pending_batches.append((offset, batch))
            else:
                self.pending_batches.append((offset, batch))
                self.pending_batches.sort(key=lambda x: x[0])
            qlen = len(self.pending_batches)

        if self.async_drain and qlen >= self.batch_size:
            self._wake_event.set()

    def drain(self, non_blocking: bool = True, timeout: float = 0.0) -> bool:
        """Drain pending batches with contiguous checkpoint tracking and WAL replay catch-up."""
        if non_blocking:
            self._wake_event.set()
            return self._drain_batches(timeout=0.005)

        deadline = time.time() + max(0.05, timeout)
        while time.time() < deadline:
            self._wake_event.set()
            self._drain_batches(timeout=0.05)
            with self._queue_lock:
                if not self.pending_batches and not self.wal_catchup_needed:
                    return True
            time.sleep(0.002)
        with self._queue_lock:
            return len(self.pending_batches) == 0 and not self.wal_catchup_needed

    def apply(
        self, batch: list[Any], offset: int, batch_start: int | None = None
    ) -> bool:
        """Deliver batch with automatic retry of pending failed offsets and strict contiguous ordering.

        When async_drain is True, queues batch and returns immediately without disk I/O.
        When async_drain is False, executes synchronous contiguous drain.
        """
        if offset >= 0:
            self.max_enqueued_wal_offset = max(self.max_enqueued_wal_offset, offset)

        with self._queue_lock:
            if batch:
                if len(self.pending_batches) < self.max_pending:
                    if (
                        not self.pending_batches
                        or offset >= self.pending_batches[-1][0]
                    ):
                        self.pending_batches.append((offset, batch))
                    else:
                        self.pending_batches.append((offset, batch))
                        self.pending_batches.sort(key=lambda x: x[0])
                else:
                    self.queue_overflow_count += 1
                    self.wal_catchup_needed = True
                    self.recovery_status = "DEGRADED"
            qlen = len(self.pending_batches)

        if self.async_drain:
            if qlen >= self.batch_size:
                self._wake_event.set()
            return True

        return self._drain_batches(timeout=0.005)

    def _drain_batches(self, timeout: float = 0.005) -> bool:
        now = time.time()
        if (
            self.consecutive_failures > 0
            and isinstance(getattr(self, "projection", None), SQLiteProjection)
            and (now - self.last_failure_ts) < 0.2
        ):
            return False

        # Non-blocking lock acquisition with short timeout to prevent feed-blocking
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            self.consecutive_failures += 1
            self.total_retries += 1
            self.last_failure_ts = now
            self.recovery_status = (
                "STALLED" if self.consecutive_failures >= 10 else "DEGRADED"
            )
            return False

        try:
            # If WAL catch-up is needed, replay missing offsets from WAL
            if (
                self.wal_catchup_needed
                and self.replay_fn is not None
                and self.log_source is not None
            ):
                cp = self.checkpoint()
                needed_from = (cp + 1) if cp >= 0 else 0
                head = getattr(self.log_source, "next_offset", 0)
                if needed_from < head:
                    try:
                        wal_batch, wal_off = self.replay_fn(needed_from)
                        if wal_batch:
                            try:
                                self.projection.apply(wal_batch, wal_off)
                            except TypeError:
                                self.projection.apply(wal_batch, wal_off)
                            if wal_off >= 0:
                                self.last_contiguous_offset = max(
                                    self.last_contiguous_offset, wal_off
                                )
                            if wal_off >= self.max_enqueued_wal_offset:
                                self.wal_catchup_needed = False
                                with self._queue_lock:
                                    self.pending_batches = [
                                        (o, b)
                                        for (o, b) in self.pending_batches
                                        if o > wal_off
                                    ]
                                    self.failed_offsets = [
                                        o for o in self.failed_offsets if o > wal_off
                                    ]
                    except Exception:
                        self.consecutive_failures += 1
                        self.total_retries += 1
                        self.last_failure_ts = time.time()
                        self.recovery_status = (
                            "STALLED" if self.consecutive_failures >= 10 else "DEGRADED"
                        )
                        return False

            can_coalesce = getattr(
                self.projection, "supports_coalescing", False
            ) or isinstance(self.projection, (SQLiteProjection, DuckDBProjection))

            # Drain pending batches in strict contiguous order
            while True:
                with self._queue_lock:
                    if not self.pending_batches:
                        break

                    # Check contiguous order before applying to projection
                    cur_cp = self.last_contiguous_offset
                    if cur_cp < 0:
                        cur_cp = self.checkpoint()
                    expected_start = getattr(
                        self.projection, "expected_start_offset", 0
                    )
                    expected_next = (cur_cp + 1) if cur_cp >= 0 else expected_start

                    p_off, p_batch = self.pending_batches[0]
                    if p_batch:
                        wal_offsets = [
                            item.offset
                            for item in p_batch
                            if hasattr(item, "offset")
                            and item.offset is not None
                            and item.offset >= 0
                        ]
                        b_start = (
                            min(wal_offsets)
                            if wal_offsets
                            else (p_off - len(p_batch) + 1 if p_off >= 0 else p_off)
                        )
                    else:
                        b_start = p_off

                    # If batch begins after expected_next, gap detected; halt contiguous drainage
                    if expected_next is not None and b_start > expected_next:
                        break

                    if can_coalesce:
                        to_apply: list[Any] = []
                        consumed_count = 0
                        last_off = -1
                        curr_expected = expected_next

                        for cand_off, cand_batch in self.pending_batches:
                            if cand_batch:
                                c_wal_offs = [
                                    item.offset
                                    for item in cand_batch
                                    if hasattr(item, "offset")
                                    and item.offset is not None
                                    and item.offset >= 0
                                ]
                                c_start = (
                                    min(c_wal_offs)
                                    if c_wal_offs
                                    else (
                                        cand_off - len(cand_batch) + 1
                                        if cand_off >= 0
                                        else cand_off
                                    )
                                )
                                c_end = max(c_wal_offs) if c_wal_offs else cand_off
                            else:
                                c_start = cand_off
                                c_end = cand_off

                            if curr_expected is not None and c_start > curr_expected:
                                break

                            to_apply.extend(cand_batch)
                            consumed_count += 1
                            last_off = cand_off
                            if c_end >= 0:
                                curr_expected = c_end + 1

                            if len(to_apply) >= self.batch_size:
                                break

                        if consumed_count == 0:
                            break
                    else:
                        to_apply = p_batch
                        consumed_count = 1
                        last_off = p_off

                try:
                    self.projection.apply(to_apply, last_off)
                    with self._queue_lock:
                        del self.pending_batches[:consumed_count]
                        for off in list(self.failed_offsets):
                            if off <= last_off:
                                self.failed_offsets.remove(off)
                    if last_off >= 0:
                        self.last_contiguous_offset = max(
                            self.last_contiguous_offset, last_off
                        )
                    self.last_success_ts = time.time()
                except Exception:
                    self.consecutive_failures += 1
                    self.total_retries += 1
                    self.last_failure_ts = time.time()
                    self.recovery_status = (
                        "STALLED" if self.consecutive_failures >= 10 else "DEGRADED"
                    )
                    with self._queue_lock:
                        if last_off not in self.failed_offsets and last_off >= 0:
                            self.failed_offsets.append(last_off)
                    return False

            if not self.wal_catchup_needed and not self.failed_offsets:
                self.consecutive_failures = 0
                self.recovery_status = "IDLE"
            return True
        finally:
            self._lock.release()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until all pending batches are committed to projection."""
        return self.drain(non_blocking=False, timeout=timeout)

    def checkpoint(self) -> int:
        if hasattr(self.projection, "checkpoint"):
            try:
                cp = self.projection.checkpoint()
                if cp >= 0:
                    self.last_contiguous_offset = max(self.last_contiguous_offset, cp)
                    return cp
            except Exception:
                pass
        return self.last_contiguous_offset

    def close(self, timeout: float = 5.0) -> None:
        """Stop worker, flush pending batches, and release tracker resources."""
        self._stop_event.set()
        self._wake_event.set()
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        self.flush(timeout=timeout)
        if hasattr(self.projection, "close"):
            try:
                self.projection.close()
            except Exception:
                pass


class SQLiteProjection:
    """Production SQLite projection with atomic row + checkpoint transaction commit."""

    supports_coalescing: bool = True

    def __init__(
        self,
        db_path: str,
        name: str = "sqlite_canonical",
        expected_start_offset: int = 0,
        busy_timeout_ms: int = 10,
    ) -> None:
        self.db_path = db_path
        self.name = name
        self.expected_start_offset = expected_start_offset
        self.busy_timeout_ms = busy_timeout_ms
        self._highest_contiguous_offset: int = -1
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            db_path,
            timeout=max(0.01, busy_timeout_ms / 1000.0),
            check_same_thread=False,
            isolation_level=None,  # Explicit transaction management
        )
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms};")
        self._conn.execute("PRAGMA cache_size = -65536;")  # 64 MB in-memory cache
        self._conn.execute("PRAGMA temp_store = MEMORY;")
        self._conn.execute("PRAGMA mmap_size = 268435456;")  # 256 MB memory-mapped I/O
        self._conn.execute("PRAGMA wal_autocheckpoint = 25000;")
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
            try:
                cur.execute(
                    "SELECT last_offset FROM projection_checkpoints WHERE name = ?;",
                    (self.name,),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    val = int(row[0])
                    if val >= 0:
                        self._highest_contiguous_offset = val
            except (ValueError, TypeError, sqlite3.OperationalError):
                self._highest_contiguous_offset = -1

    def checkpoint(self) -> int:
        """Return the latest committed offset, or -1 if no checkpoint exists."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "SELECT last_offset FROM projection_checkpoints WHERE name = ?;",
                    (self.name,),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    return int(row[0])
            except Exception:
                pass
            return -1

    def apply(
        self,
        batch: list[Any],
        offset: int,
        max_retries: int = 1,
        retry_delay: float = 0.005,
    ) -> None:
        """Atomically persist rows and advance checkpoint in a single SQLite transaction with retry on lock/busy."""
        if not batch and offset < 0:
            return

        acquired = self._lock.acquire(timeout=1.0)
        if not acquired:
            raise sqlite3.OperationalError("database is locked (projection mutex busy)")
        try:
            for attempt in range(max_retries):
                cur = self._conn.cursor()
                in_transaction = False
                try:
                    cur.execute("BEGIN IMMEDIATE;")
                    in_transaction = True

                    # Query authoritative checkpoint under transaction lock (cached in memory)
                    cur_cp = self._highest_contiguous_offset
                    if cur_cp < 0:
                        try:
                            cur.execute(
                                "SELECT last_offset FROM projection_checkpoints WHERE name = ?;",
                                (self.name,),
                            )
                            row = cur.fetchone()
                            if row and row[0] is not None:
                                db_cp = int(row[0])
                                cur_cp = max(cur_cp, db_cp)
                                self._highest_contiguous_offset = cur_cp
                        except (ValueError, TypeError, sqlite3.OperationalError):
                            pass

                    expected_next = (
                        (cur_cp + 1) if cur_cp >= 0 else self.expected_start_offset
                    )

                    if offset >= 0:
                        wal_offsets = [
                            item.offset
                            for item in batch
                            if hasattr(item, "offset")
                            and item.offset is not None
                            and item.offset >= 0
                        ]
                        if wal_offsets:
                            batch_start = min(wal_offsets)
                            batch_end = max(wal_offsets)
                        else:
                            batch_len = len(batch) if batch else 1
                            batch_start = offset - batch_len + 1
                            batch_end = offset

                        # Invariant (P0-A): Reject batches that begin after a missing initial or middle offset
                        if batch_start > expected_next:
                            raise ValueError(
                                f"Projection gap detected for '{self.name}': "
                                f"expected start offset <= {expected_next}, "
                                f"got batch_start {batch_start} (batch_end={batch_end}, cur_checkpoint={cur_cp})"
                            )

                    canonical_rows: list[tuple] = []
                    quarantine_rows: list[tuple] = []

                    for item in batch:
                        if isinstance(item, EngineDecision):
                            if (
                                item.canonical_event
                                and item.quality_status != QualityStatus.INVALID
                            ):
                                ev = item.canonical_event
                                canonical_rows.append(
                                    (
                                        ev.event_id,
                                        ev.instrument_id,
                                        ev.event_type.value
                                        if hasattr(ev.event_type, "value")
                                        else str(ev.event_type),
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
                                        ev.quality_status.value
                                        if hasattr(ev.quality_status, "value")
                                        else str(ev.quality_status),
                                        json.dumps(ev.reasons),
                                        str(ev.raw_id)
                                        if ev.raw_id is not None
                                        else None,
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
                                        item.event_type.value
                                        if hasattr(item.event_type, "value")
                                        else str(item.event_type),
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
                                        item.quality_status.value
                                        if hasattr(item.quality_status, "value")
                                        else str(item.quality_status),
                                        json.dumps(item.reasons),
                                        str(item.raw_id)
                                        if item.raw_id is not None
                                        else None,
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
                    # Critical checkpoint invariant: never advance beyond highest contiguous committed offset!
                    # Only advance if the new offset strictly exceeds current checkpoint (idempotent for duplicates)
                    if offset >= 0 and offset > cur_cp:
                        self._highest_contiguous_offset = max(
                            self._highest_contiguous_offset, offset
                        )
                        cur.execute(
                            """
                            INSERT OR REPLACE INTO projection_checkpoints (name, last_offset, updated_at)
                            VALUES (?, ?, ?);
                            """,
                            (
                                self.name,
                                self._highest_contiguous_offset,
                                time.time(),
                            ),
                        )

                    cur.execute("COMMIT;")
                    return
                except (ValueError, TypeError):
                    if in_transaction:
                        try:
                            cur.execute("ROLLBACK;")
                            self._highest_contiguous_offset = -1
                        except Exception:
                            pass
                    raise
                except sqlite3.OperationalError as exc:
                    if in_transaction:
                        try:
                            cur.execute("ROLLBACK;")
                            self._highest_contiguous_offset = -1
                        except Exception:
                            pass
                    msg = str(exc).lower()
                    if attempt < max_retries - 1 and ("locked" in msg or "busy" in msg):
                        time.sleep(retry_delay * (2**attempt))
                        continue
                    raise
                except Exception:
                    if in_transaction:
                        try:
                            cur.execute("ROLLBACK;")
                            self._highest_contiguous_offset = -1
                        except Exception:
                            pass
                    raise
        finally:
            self._lock.release()

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

    def query(self, sql: str, params: list | tuple | None = None) -> list:
        """Execute a read query on the SQLite projection."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params or [])
            return cur.fetchall()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


class DuckDBProjection:
    """Production DuckDB columnar projection for ultra-fast time-series analytical scans."""

    supports_coalescing: bool = True

    def __init__(
        self,
        db_path: str = ":memory:",
        name: str = "duckdb_canonical",
        expected_start_offset: int | None = None,
    ) -> None:
        try:
            import duckdb
        except ImportError:
            raise RuntimeError(
                "duckdb is not installed; install via pip install duckdb"
            )
        self.db_path = db_path
        self.name = name
        self.expected_start_offset = expected_start_offset
        self._highest_contiguous_offset: int = -1
        self._lock = threading.RLock()
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
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantine (
                    event_id VARCHAR PRIMARY KEY,
                    instrument_id VARCHAR NOT NULL,
                    source VARCHAR NOT NULL,
                    quality_status VARCHAR NOT NULL,
                    reasons VARCHAR,
                    payload_json VARCHAR,
                    receive_timestamp DOUBLE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_duck_inst_ts 
                ON canonical_events(instrument_id, exchange_timestamp);
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
        if not batch and offset < 0:
            return
        canonical_rows = []
        quarantine_rows = []
        for item in batch:
            ev = (
                item
                if isinstance(item, CanonicalEvent)
                else getattr(item, "canonical_event", None)
            )
            if ev is not None:
                r_json = (
                    json.dumps(ev.reasons)
                    if isinstance(ev.reasons, list)
                    else str(ev.reasons or "")
                )
                q_status_str = (
                    ev.quality_status.value
                    if hasattr(ev.quality_status, "value")
                    else str(ev.quality_status)
                )
                if q_status_str == "INVALID":
                    quarantine_rows.append(
                        (
                            ev.event_id,
                            ev.instrument_id,
                            ev.source,
                            q_status_str,
                            r_json,
                            json.dumps({"raw_id": ev.raw_id}),
                            float(ev.receive_timestamp or 0.0),
                        )
                    )
                else:
                    ev_type_str = (
                        ev.event_type.value
                        if hasattr(ev.event_type, "value")
                        else str(ev.event_type)
                    )
                    canonical_rows.append(
                        (
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
                            q_status_str,
                            r_json,
                            str(ev.raw_id) if ev.raw_id is not None else None,
                        )
                    )
        with self._lock:
            cur_cp = self.checkpoint()
            if offset >= 0:
                wal_offsets = [
                    item.offset
                    for item in batch
                    if hasattr(item, "offset")
                    and item.offset is not None
                    and item.offset >= 0
                ]
                if wal_offsets:
                    batch_start = min(wal_offsets)
                    batch_end = max(wal_offsets)
                else:
                    batch_len = len(batch) if batch else 1
                    batch_start = offset - batch_len + 1
                    batch_end = offset

                if cur_cp >= 0:
                    expected_next = cur_cp + 1
                    if batch_start > expected_next:
                        raise ValueError(
                            f"Projection gap detected for '{self.name}': "
                            f"expected start offset <= {expected_next}, "
                            f"got batch_start {batch_start} (batch_end={batch_end}, cur_checkpoint={cur_cp})"
                        )
                elif self.expected_start_offset is not None:
                    expected_next = self.expected_start_offset
                    if batch_start > expected_next:
                        raise ValueError(
                            f"Projection gap detected for '{self.name}': "
                            f"expected start offset <= {expected_next}, "
                            f"got batch_start {batch_start} (batch_end={batch_end}, cur_checkpoint={cur_cp})"
                        )

            if canonical_rows:
                self._conn.executemany(
                    """
                    INSERT OR IGNORE INTO canonical_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    canonical_rows,
                )
            if quarantine_rows:
                self._conn.executemany(
                    """
                    INSERT OR IGNORE INTO quarantine VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    quarantine_rows,
                )
            if offset >= 0 and offset > cur_cp:
                self._highest_contiguous_offset = max(
                    self._highest_contiguous_offset, offset
                )
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO projection_checkpoints VALUES (?, ?, ?);
                    """,
                    [self.name, self._highest_contiguous_offset, time.time()],
                )

    def count_canonical(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[
                    0
                ]
            )

    def count_quarantine(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
            )

    def query(self, sql: str, params: list | None = None) -> list:
        with self._lock:
            return self._conn.execute(sql, params or []).fetchall()

    def export_parquet(self, parquet_path: str, compression: str = "snappy") -> int:
        """Export canonical_events table to a compressed Apache Parquet file."""
        with self._lock:
            p_abs = os.path.abspath(parquet_path)
            os.makedirs(os.path.dirname(p_abs), exist_ok=True)
            self._conn.execute(
                f"COPY canonical_events TO '{p_abs}' (FORMAT PARQUET, COMPRESSION '{compression}')"
            )
            count = int(
                self._conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[
                    0
                ]
            )
            return count

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


def export_journal_to_parquet(
    journal_dir: str, parquet_path: str, compression: str = "snappy"
) -> int:
    """Scan IngestLog segments and export canonical events to compressed Apache Parquet."""
    from .ingestlog import IngestLog
    from .quality import QualityEngine

    log = IngestLog(journal_dir)
    qe = QualityEngine()
    p_abs = os.path.abspath(parquet_path)
    os.makedirs(os.path.dirname(p_abs), exist_ok=True)

    proj = DuckDBProjection(db_path=":memory:")
    batch = []
    scanned = 0
    for offset, raw in log.iter_from(0):
        scanned += 1
        p = raw.payload
        ev = CanonicalEvent(
            event_id=f"wal_{offset}",
            instrument_id=p.get("instrument", ""),
            event_type=p.get("event_type", "TRADE"),
            exchange_timestamp=float(p.get("exchange_ts", raw.receive_timestamp)),
            receive_timestamp=float(raw.receive_timestamp),
            processing_timestamp=time.time(),
            source=raw.source,
            sequence_number=p.get("sequence"),
            price=p.get("price"),
            quantity=p.get("quantity"),
            bid_price=p.get("bid"),
            ask_price=p.get("ask"),
            quality_status=QualityStatus.VALID,
            reasons=[],
            raw_id=str(offset),
        )
        evaluated = qe.evaluate(ev)
        batch.append(evaluated if evaluated is not None else ev)
        if len(batch) >= 2000:
            proj.apply(batch, offset=offset)
            batch.clear()

    if batch:
        proj.apply(batch, offset=scanned - 1)
        batch.clear()

    count = proj.export_parquet(p_abs, compression=compression)
    proj.close()
    return count


class AsyncProjectionWorker:
    """Background worker isolating analytical projections from capture engine latency.

    Engine pushes validated canonical events or decisions into an isolated queue.
    A background daemon thread drains batches into the underlying projection (SQLite or DuckDB).
    Analytics query locks, disk IO delays, or projection crashes CANNOT block or poison
    the primary ingest engine or IngestLog WAL.
    """

    def __init__(
        self,
        projection: Projection,
        max_queue_size: int = 20_000,
        batch_size: int = 500,
        flush_interval_sec: float = 0.05,
        max_retries: int = 3,
        retry_backoff_sec: float = 0.02,
    ) -> None:
        self.projection = projection
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.max_retries = max_retries
        self.retry_backoff_sec = retry_backoff_sec

        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._items_enqueued: int = 0
        self._items_committed: int = 0
        self._batches_committed: int = 0
        self._drop_count: int = 0
        self._error_count: int = 0
        self._last_committed_offset: int = -1
        self._highest_enqueued_offset: int = -1
        self._wal_catchup_needed: bool = False
        self._flush_trigger = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="mdrap-async-projection"
        )
        self._worker_thread.start()

    def enqueue(self, item: Any, offset: int = -1) -> bool:
        """Non-blocking enqueue of an event/decision.

        If the queue is saturated, drops the item and increments drop_count to preserve
        strict backpressure isolation without blocking the primary capture engine.
        """
        try:
            self._queue.put_nowait((item, offset))
            self._items_enqueued += 1
            if offset > self._highest_enqueued_offset:
                self._highest_enqueued_offset = offset
            return True
        except queue.Full:
            self._drop_count += 1
            self._wal_catchup_needed = True
            logger.warning(
                "[AsyncProjection] Queue full (%d items); dropping analytical projection event #%d; WAL catch-up flagged",
                self.max_queue_size,
                self._drop_count,
            )
            return False

    def _run_loop(self) -> None:
        batch: list[Any] = []
        highest_offset = -1
        last_flush = time.time()

        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                timeout = max(
                    0.005, self.flush_interval_sec - (time.time() - last_flush)
                )
                item, offset = self._queue.get(timeout=timeout)
                batch.append(item)
                if offset > highest_offset:
                    highest_offset = offset
                self._queue.task_done()
            except queue.Empty:
                pass

            now = time.time()
            time_to_flush = now - last_flush >= self.flush_interval_sec
            batch_full = len(batch) >= self.batch_size
            stopping = self._stop_event.is_set()
            flush_triggered = self._flush_trigger.is_set()

            if batch and (time_to_flush or batch_full or stopping or flush_triggered):
                for attempt in range(self.max_retries):
                    try:
                        self.projection.apply(batch, offset=highest_offset)
                        self._items_committed += len(batch)
                        self._batches_committed += 1
                        self._last_committed_offset = highest_offset
                        break
                    except Exception as exc:
                        if attempt < self.max_retries - 1:
                            time.sleep(self.retry_backoff_sec * (2**attempt))
                        else:
                            self._error_count += 1
                            logger.error(
                                "[AsyncProjection] Failed to apply projection batch after %d attempts: %s",
                                self.max_retries,
                                exc,
                            )
                batch.clear()
                last_flush = time.time()
                self._flush_trigger.clear()

    def checkpoint(self) -> int:
        """Return the latest committed log offset from the projection or worker."""
        if hasattr(self.projection, "checkpoint"):
            return self.projection.checkpoint()
        return self._last_committed_offset

    def stats(self) -> dict[str, Any]:
        """Return operational telemetry metrics including projection lag."""
        lag = (
            max(0, self._highest_enqueued_offset - self._last_committed_offset)
            if self._highest_enqueued_offset >= 0
            else 0
        )
        return {
            "queue_depth": self._queue.qsize(),
            "items_enqueued": self._items_enqueued,
            "items_committed": self._items_committed,
            "batches_committed": self._batches_committed,
            "drops_total": self._drop_count,
            "errors_total": self._error_count,
            "last_committed_offset": self._last_committed_offset,
            "highest_enqueued_offset": self._highest_enqueued_offset,
            "lag_offsets": lag,
            "wal_catchup_needed": self._wal_catchup_needed,
            "is_lagging": lag > 1000
            or self._queue.qsize() > (self.max_queue_size // 2),
            "is_alive": self._worker_thread.is_alive(),
        }

    def flush(self, timeout: float = 5.0) -> None:
        """Wait until all currently queued and batched items are durably committed."""
        self._flush_trigger.set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._flush_trigger.set()
            if (
                self._queue.empty()
                and self._last_committed_offset >= self._highest_enqueued_offset
            ):
                break
            time.sleep(0.005)

    def close(self, timeout: float = 5.0) -> None:
        """Stop the background worker thread gracefully and flush pending writes."""
        self._stop_event.set()
        self._worker_thread.join(timeout=timeout)
        if hasattr(self.projection, "close"):
            self.projection.close()


def rebuild_projection_from_wal(
    journal_dir: str,
    projection: Projection,
    start_offset: int = 0,
    batch_size: int = 2000,
) -> int:
    """Rebuild an analytical projection (SQLite, DuckDB, etc.) from the immutable IngestLog WAL.

    Invariant:
        The IngestLog WAL is the authoritative source of truth. Projections are ephemeral,
        rebuildable materialized views. If a projection database is corrupted, deleted,
        or lagging, it can be deterministically replayed and reconstructed from the WAL.

    Args:
        journal_dir: Path to directory containing IngestLog WAL segments.
        projection: The target Projection instance (e.g. SQLiteProjection).
        start_offset: Offset from which to begin replay (default: 0).
        batch_size: Number of records to accumulate before invoking projection.apply().

    Returns:
        Total number of records applied to the projection.
    """
    from .ingestlog import IngestLog
    from .engine import Engine

    log = IngestLog(journal_dir)
    engine = Engine()
    state = engine.create_initial_state()

    batch: list[Any] = []
    applied_count = 0
    highest_offset = -1

    for offset, raw in log.iter_from(start_offset):
        state, decision = engine.step(state, raw, engine.clock, offset=offset)
        batch.append(decision)
        highest_offset = offset
        applied_count += 1

        if len(batch) >= batch_size:
            projection.apply(batch, offset=highest_offset)
            batch.clear()

    if batch:
        projection.apply(batch, offset=highest_offset)
        batch.clear()

    return applied_count
