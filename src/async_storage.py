"""
Dedicated Asynchronous Storage Worker for MDRAP.

Decouples synchronous SQLite and DuckDB disk commits (fsync) from the real-time
market data processing loop. Canonical events, quarantine tuples, and lineage
records are enqueued into a lock-free SPSC ring buffer and flushed in batches
by a dedicated background worker thread.

Zero disk I/O pauses on the tick broadcasting hot path.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional, Tuple, Union

from models import CanonicalEvent
from spsc_ring import SPSCRingBuffer
from storage import Store

logger = logging.getLogger(__name__)

# Action tags for storage worker
OP_CANONICAL = 1
OP_QUARANTINE = 2
OP_LINEAGE = 3
OP_HEALTH = 4
OP_FLUSH_BARRIER = 5


class StorageCommand:
    __slots__ = ("op_type", "payload")

    def __init__(self, op_type: int, payload: Any):
        self.op_type = op_type
        self.payload = payload


class AsyncStorageWorker:
    """
    Background worker thread that drains persistence commands from an SPSC ring buffer
    and commits them to SQLite in optimized batches.
    """

    def __init__(
        self,
        store: Store,
        queue_capacity: int = 65536,
        batch_size: int = 2000,
        flush_interval_s: float = 0.25,
        duck_path: Optional[str] = None,
    ):
        self.store = store
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.duck_path = duck_path

        self._queue: SPSCRingBuffer[StorageCommand] = SPSCRingBuffer(capacity=queue_capacity)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._flush_lock = threading.Lock()
        self._flush_complete_event = threading.Event()

        # Telemetry counters
        self._total_canonical = 0
        self._total_quarantine = 0
        self._total_lineage = 0
        self._total_commits = 0
        self._last_commit_ms = 0.0
        self._max_commit_ms = 0.0
        self._total_dropped = 0

    @property
    def queue(self) -> SPSCRingBuffer[StorageCommand]:
        return self._queue

    def start(self) -> None:
        """Start the background storage thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="mdrap-async-storage", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop worker and drain all pending storage writes."""
        if not self._running:
            return
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

        # Final synchronous flush of any residual queue items
        self._drain_and_commit(force_all=True)

    def write_canonical(self, event: CanonicalEvent) -> bool:
        """Enqueue a validated canonical event for background SQLite storage."""
        cmd = StorageCommand(OP_CANONICAL, event)
        ok = self._queue.offer(cmd)
        if not ok:
            self._total_dropped += 1
        return ok

    def write_quarantine(self, row: tuple) -> bool:
        """Enqueue an invalid/suspicious quarantine record."""
        cmd = StorageCommand(OP_QUARANTINE, row)
        ok = self._queue.offer(cmd)
        if not ok:
            self._total_dropped += 1
        return ok

    def write_lineage(self, row: tuple) -> bool:
        """Enqueue an audit lineage trace."""
        cmd = StorageCommand(OP_LINEAGE, row)
        ok = self._queue.offer(cmd)
        if not ok:
            self._total_dropped += 1
        return ok

    def write_health(self, rows: list) -> bool:
        """Enqueue source health telemetry."""
        cmd = StorageCommand(OP_HEALTH, rows)
        ok = self._queue.offer(cmd)
        if not ok:
            self._total_dropped += 1
        return ok

    def flush(self, timeout: float = 2.0) -> None:
        """Synchronously wait until all currently queued events are committed to disk."""
        if not self._running:
            self._drain_and_commit(force_all=True)
            return

        with self._flush_lock:
            self._flush_complete_event.clear()
            barrier = StorageCommand(OP_FLUSH_BARRIER, self._flush_complete_event)
            while not self._queue.offer(barrier):
                time.sleep(0.0005)
            self._flush_complete_event.wait(timeout=timeout)

    def _worker_loop(self) -> None:
        """Main background loop draining SPSC queue and committing in batches."""
        last_flush = time.time()
        canonical_batch: List[CanonicalEvent] = []
        quarantine_batch: List[tuple] = []
        lineage_batch: List[tuple] = []
        health_batch: List[tuple] = []

        while self._running:
            cmd = self._queue.poll()
            if cmd is not None:
                if cmd.op_type == OP_CANONICAL:
                    canonical_batch.append(cmd.payload)
                elif cmd.op_type == OP_QUARANTINE:
                    quarantine_batch.append(cmd.payload)
                elif cmd.op_type == OP_LINEAGE:
                    lineage_batch.append(cmd.payload)
                elif cmd.op_type == OP_HEALTH:
                    health_batch.extend(cmd.payload)
                elif cmd.op_type == OP_FLUSH_BARRIER:
                    # Flush immediately on barrier
                    self._commit_batches(canonical_batch, quarantine_batch, lineage_batch, health_batch)
                    last_flush = time.time()
                    if isinstance(cmd.payload, threading.Event):
                        cmd.payload.set()
                    continue

            now = time.time()
            total_pending = len(canonical_batch) + len(quarantine_batch) + len(lineage_batch)
            time_elapsed = (now - last_flush) >= self.flush_interval_s

            if total_pending >= self.batch_size or (total_pending > 0 and time_elapsed):
                self._commit_batches(canonical_batch, quarantine_batch, lineage_batch, health_batch)
                last_flush = now
            elif cmd is None:
                # Yield CPU briefly if queue was empty
                time.sleep(0.001)

        # Drain any residual items on exit
        self._commit_batches(canonical_batch, quarantine_batch, lineage_batch, health_batch)

    def _commit_batches(
        self,
        canonical_batch: List[CanonicalEvent],
        quarantine_batch: List[tuple],
        lineage_batch: List[tuple],
        health_batch: List[tuple],
    ) -> None:
        if not canonical_batch and not quarantine_batch and not lineage_batch and not health_batch:
            return

        t0 = time.perf_counter()
        try:
            if canonical_batch:
                self.store.write_canonical_batch(canonical_batch)
                self._total_canonical += len(canonical_batch)
                canonical_batch.clear()

            if quarantine_batch:
                self.store.write_quarantine_batch(quarantine_batch)
                self._total_quarantine += len(quarantine_batch)
                quarantine_batch.clear()

            if lineage_batch:
                self.store.write_lineage_batch(lineage_batch)
                self._total_lineage += len(lineage_batch)
                lineage_batch.clear()

            if health_batch:
                self.store.upsert_source_health(health_batch)
                health_batch.clear()

            self.store.commit()
            self._total_commits += 1
            dur_ms = (time.perf_counter() - t0) * 1000.0
            self._last_commit_ms = round(dur_ms, 2)
            if dur_ms > self._max_commit_ms:
                self._max_commit_ms = round(dur_ms, 2)
        except Exception as e:
            logger.error(f"Async storage batch commit failed: {e}")

    def _drain_and_commit(self, force_all: bool = True) -> None:
        """Synchronous drain of the queue for shutdown or offline testing."""
        canonical_batch: List[CanonicalEvent] = []
        quarantine_batch: List[tuple] = []
        lineage_batch: List[tuple] = []
        health_batch: List[tuple] = []

        while True:
            cmd = self._queue.poll()
            if cmd is None:
                break
            if cmd.op_type == OP_CANONICAL:
                canonical_batch.append(cmd.payload)
            elif cmd.op_type == OP_QUARANTINE:
                quarantine_batch.append(cmd.payload)
            elif cmd.op_type == OP_LINEAGE:
                lineage_batch.append(cmd.payload)
            elif cmd.op_type == OP_HEALTH:
                health_batch.extend(cmd.payload)
            elif cmd.op_type == OP_FLUSH_BARRIER:
                if isinstance(cmd.payload, threading.Event):
                    cmd.payload.set()

        self._commit_batches(canonical_batch, quarantine_batch, lineage_batch, health_batch)

    def stats(self) -> dict:
        """Return operational telemetry."""
        return {
            "is_running": self._running,
            "queue_size": self._queue.size(),
            "queue_capacity": self._queue.capacity,
            "total_canonical": self._total_canonical,
            "total_quarantine": self._total_quarantine,
            "total_lineage": self._total_lineage,
            "total_commits": self._total_commits,
            "last_commit_ms": self._last_commit_ms,
            "max_commit_ms": self._max_commit_ms,
            "total_dropped": self._total_dropped,
        }
