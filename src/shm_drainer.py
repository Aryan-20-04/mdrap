"""
MDRAP Asynchronous Shared-Memory Drain Worker (Phase 4).

Decouples the ultra-fast real-time ingestion/quality hot path from disk I/O and
database transactions. Reads committed slots from the lock-free circular SHM ring
buffer and asynchronously drains them into the append-only binary journal and/or
relational analytical storage (Spec §5, §26).
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Any

from shm import SHMReader, SLOT_SIZE, HEADER_SIZE
from journal import BinaryJournal
from models import CanonicalEvent, EventType, QualityStatus

logger = logging.getLogger("mdrap.shm_drainer")


class DrainStats:
    """Telemetry counters for SHMDrainWorker."""

    def __init__(self):
        self.drained_count: int = 0
        self.drain_lag: int = 0
        self.max_drain_lag: int = 0
        self.watermark_alerts: int = 0
        self.laps_detected: int = 0
        self.last_drained_seq: int = 0
        self.batches_flushed: int = 0
        self.elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "drained_count": self.drained_count,
            "drain_lag": self.drain_lag,
            "max_drain_lag": self.max_drain_lag,
            "watermark_alerts": self.watermark_alerts,
            "laps_detected": self.laps_detected,
            "last_drained_seq": self.last_drained_seq,
            "batches_flushed": self.batches_flushed,
            "elapsed_sec": round(self.elapsed_sec, 4),
            "drain_eps": round(self.drained_count / max(0.001, self.elapsed_sec), 1),
        }


class SHMDrainWorker:
    """
    Asynchronous daemon thread draining lock-free SHM ring buffer slots into
    high-performance append-only journal and/or relational database stores.
    """

    def __init__(
        self,
        shm_name: str = "mdrap_feed",
        journal_path: str | None = None,
        store: Any | None = None,
        batch_size: int = 1000,
        flush_interval_s: float = 0.10,
        poll_spin_budget: int = 2000,
    ):
        self.shm_name = shm_name
        self.journal_path = journal_path
        self.store = store
        self.batch_size = max(1, batch_size)
        self.flush_interval_s = flush_interval_s
        self.poll_spin_budget = poll_spin_budget

        self.reader = SHMReader(name=shm_name)
        self.journal: BinaryJournal | None = None
        if journal_path:
            self.journal = BinaryJournal(journal_path)

        self.stats = DrainStats()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_seq: int = 0
        self._pending_canonical: list[CanonicalEvent] = []
        self._t_start: float = time.time()

    def start(self, start_seq: int | None = None) -> SHMDrainWorker:
        """Launch background drain thread."""
        if self._thread and self._thread.is_alive():
            return self

        if start_seq is not None:
            self._current_seq = start_seq
        else:
            latest = self.reader.read_latest_seq()
            # Start from slot 0 or latest - buffer size if ring wrapped
            self._current_seq = (
                max(0, latest - self.reader.slot_count + 1)
                if latest > self.reader.slot_count
                else 0
            )

        self._stop_event.clear()
        self._t_start = time.time()
        self._thread = threading.Thread(
            target=self._drain_loop,
            daemon=True,
            name=f"shm-drainer-{self.shm_name}",
        )
        self._thread.start()
        return self

    def _drain_loop(self) -> None:
        """Core asynchronous polling and batch flushing loop."""
        last_flush_ts = time.time()
        spin_count = 0

        while not self._stop_event.is_set():
            drained_in_cycle = 0

            while drained_in_cycle < self.batch_size:
                head = self.reader.read_latest_seq() + 1
                if head > self._current_seq:
                    lag = head - self._current_seq
                    self.stats.drain_lag = lag
                    if lag > self.stats.max_drain_lag:
                        self.stats.max_drain_lag = lag

                    # Overrun check: if writer completely lapped the drainer
                    if lag > self.reader.slot_count:
                        self.stats.laps_detected += 1
                        self._current_seq = head - self.reader.slot_count

                    if lag >= self.reader.watermark_slots:
                        self.stats.watermark_alerts += 1

                # Fast zero-copy slot read with seqlock double-read
                slot_idx = self._current_seq & self.reader.mask
                offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)
                c1 = struct.unpack_from("<Q", self.reader.shm.buf, offset)[0]
                if c1 != self._current_seq:
                    break

                raw_slot = bytes(self.reader.shm.buf[offset : offset + SLOT_SIZE])
                c2 = struct.unpack_from("<Q", self.reader.shm.buf, offset)[0]
                if c2 != c1:
                    break

                # 1. Drain directly to memory-mapped binary journal
                if self.journal:
                    self.journal.append_raw_slot(raw_slot)

                # 2. Accumulate canonical events for relational store (only if store enabled)
                if self.store:
                    slot_dict = self.reader.read_slot(self._current_seq)
                    if slot_dict:
                        self._pending_canonical.append(
                            self._to_canonical_event(slot_dict)
                        )

                self.stats.drained_count += 1
                self.stats.last_drained_seq = self._current_seq
                self._current_seq += 1
                drained_in_cycle += 1
                spin_count = 0

            # Flush batches to disk / store
            now = time.time()
            if drained_in_cycle > 0 or (now - last_flush_ts) >= self.flush_interval_s:
                self._flush_batches()
                last_flush_ts = now

            if drained_in_cycle == 0:
                # Spin-then-sleep backoff
                spin_count += 1
                if spin_count < self.poll_spin_budget:
                    pass
                elif spin_count < self.poll_spin_budget * 2:
                    time.sleep(0.00005)  # 50 µs
                else:
                    time.sleep(0.001)  # 1 ms

        # Final drain of remaining items upon stop
        self._flush_batches()
        if self.journal:
            self.journal.flush()
        self.stats.elapsed_sec = time.time() - self._t_start

    def _flush_batches(self) -> None:
        """Commit pending records to disk and database."""
        if self.journal:
            self.journal.flush()

        if self.store and self._pending_canonical:
            batch = self._pending_canonical
            self._pending_canonical = []
            try:
                if hasattr(self.store, "write_batches_atomic"):
                    self.store.write_batches_atomic(canonical=batch)
                elif hasattr(self.store, "write_canonical_batch"):
                    self.store.write_canonical_batch(batch)
                    self.store.commit()
            except Exception as exc:
                logger.error("Error writing batch to store: %s", exc)

        self.stats.batches_flushed += 1
        self.stats.elapsed_sec = time.time() - self._t_start

    def _to_canonical_event(self, slot_dict: dict) -> CanonicalEvent:
        """Convert an SHM slot dictionary into a typed CanonicalEvent."""
        st_str = slot_dict.get("status", "VALID")
        q_status = (
            QualityStatus.VALID
            if st_str == "VALID"
            else (
                QualityStatus.SUSPICIOUS
                if st_str == "SUSPICIOUS"
                else QualityStatus.INVALID
            )
        )
        return CanonicalEvent(
            event_id=f"shm_{slot_dict['seq']}",
            instrument_id=slot_dict.get("sym", "UNKNOWN"),
            event_type=EventType.DEPTH
            if slot_dict.get("type") == "DEPTH"
            else EventType.TRADE,
            exchange_timestamp=slot_dict.get("exchange_ts", 0.0),
            receive_timestamp=slot_dict.get("ingest_ts", 0.0),
            processing_timestamp=slot_dict.get("broadcast_ts", 0.0),
            source=slot_dict.get("source", "FEED"),
            sequence_number=slot_dict.get("seq"),
            price=slot_dict.get("price"),
            quantity=slot_dict.get("size"),
            bid_price=slot_dict.get("bid"),
            ask_price=slot_dict.get("ask"),
            bid_size=slot_dict.get("bid_size"),
            ask_size=slot_dict.get("ask_size"),
            quality_status=q_status,
            reasons=[],
            raw_id=f"raw_{slot_dict['seq']}",
        )

    def drain_until(self, target_seq: int, timeout: float = 5.0) -> bool:
        """Block until the drain worker has drained up to target_seq."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.stats.last_drained_seq >= target_seq:
                self._flush_batches()
                return True
            time.sleep(0.001)
        return self.stats.last_drained_seq >= target_seq

    def stop(self, timeout: float = 5.0) -> None:
        """Signal worker to stop and wait for background thread to exit."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._flush_batches()

    def close(self) -> None:
        """Release reader and journal handles."""
        self.stop()
        if self.journal:
            self.journal.close()
            self.journal = None
        if self.reader:
            self.reader.close()

    def __enter__(self) -> SHMDrainWorker:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
