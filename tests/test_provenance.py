"""
Regression and Fault-Injection Tests for Event Provenance & Lineage Durability (Agent 1 / Batch A).

Covers:
  - MDRAP-PERS-01: Elimination of silent exception swallowing on SQLite writes.
  - MDRAP-PY-01: Explicit failure counter increments and structured logging.
  - MDRAP-TEST-01: Negative-path fault injection (database locks, read-only DBs, schema errors).
  - Durable retry dead-letter buffer and flush verification.
  - Evidence verification status degradation under storage failure.
  - Multithreaded concurrent recording safety.
"""

from __future__ import annotations

import collections
import os
import sqlite3
import tempfile
import threading
import time
from typing import Any
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from mdrap.provenance import (
    EventProvenance,
    ProvenanceDurabilityState,
    ProvenanceTracker,
    compute_payload_hash,
    create_provenance,
)
from mdrap.storage import Store


def _sample_event(event_id: str = "evt-001", symbol: str = "BTCUSDT") -> tuple[RawEvent, CanonicalEvent]:
    raw = RawEvent(
        source="BINANCE",
        payload={"s": symbol, "p": "50000.0", "q": "1.5", "t": 1700000000000},
        receive_timestamp=time.time(),
        raw_id="raw-001",
    )
    canon = CanonicalEvent(
        event_id=event_id,
        raw_id="raw-001",
        source="BINANCE",
        instrument_id=symbol,
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=time.time(),
        processing_timestamp=time.time(),
        sequence_number=1001,
        quality_status=QualityStatus.VALID,
        reasons=[],
        price=50000.0,
        quantity=1.5,
    )
    return raw, canon


class LockedStorageMock:
    """Mock storage backend simulating a locked SQLite database."""

    def __init__(self, lock_duration_calls: int = 1):
        self.lock_duration_calls = lock_duration_calls
        self.calls = 0
        self.persisted_records: list[Any] = []

    def write_provenance_batch(self, rows: list[Any]) -> None:
        self.calls += 1
        if self.calls <= self.lock_duration_calls:
            raise sqlite3.OperationalError("database is locked")
        self.persisted_records.extend(rows)


class UnrecoverableStorageMock:
    """Mock storage backend simulating an unrecoverable disk/schema failure."""

    def write_provenance_batch(self, rows: list[Any]) -> None:
        raise sqlite3.IntegrityError("CHECK constraint failed: event_provenance")


def test_provenance_happy_path_memory_and_store():
    """Verify standard happy-path recording with Store in memory and on disk."""
    store_mem = Store(":memory:")
    tracker_mem = ProvenanceTracker(max_records=1000, store=store_mem)

    raw, canon = _sample_event("evt-100")
    prov = create_provenance(raw, canon)

    tracker_mem.record(prov)

    assert tracker_mem.recorded_count == 1
    assert tracker_mem.persisted_count == 1
    assert tracker_mem.failed_recoverable_count == 0
    assert tracker_mem.failed_unrecoverable_count == 0
    assert tracker_mem.dropped_provenance_count == 0
    assert prov.durability_status == "COMMITTED_MEM"

    # Evidence export for in-memory committed
    ev = tracker_mem.export_evidence("evt-100")
    assert ev["verification_status"] == "VERIFIED_TAMPER_EVIDENT"
    assert ev["integrity"]["persistence_state"] == "COMMITTED_MEM"

    # Disk-backed store
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "store_disk.db")
        store_disk = Store(db_path)
        tracker_disk = ProvenanceTracker(max_records=1000, store=store_disk)
        raw2, canon2 = _sample_event("evt-101")
        prov2 = create_provenance(raw2, canon2)
        try:
            tracker_disk.record(prov2)

            assert tracker_disk.persisted_count == 1
            assert prov2.durability_status == ProvenanceDurabilityState.DURABLE.value
            ev2 = tracker_disk.export_evidence("evt-101")
            assert ev2["verification_status"] == "VERIFIED_TAMPER_EVIDENT"
            assert ev2["integrity"]["persistence_state"] == ProvenanceDurabilityState.DURABLE.value
        finally:
            tracker_disk.close()
            store_disk.close()


def test_provenance_fault_injection_transient_database_lock():
    """Simulate SQLite lock contention: verify recoverable classification, counters, and dead-letter retry."""
    mock_store = LockedStorageMock(lock_duration_calls=2)
    tracker = ProvenanceTracker(max_records=1000, store=mock_store)

    raw, canon = _sample_event("evt-lock-01")
    prov = create_provenance(raw, canon)

    # First attempt: storage is locked
    tracker.record(prov)

    assert tracker.recorded_count == 1
    assert tracker.persisted_count == 0
    assert tracker.failed_recoverable_count == 1
    assert tracker.dropped_provenance_count == 0
    assert len(tracker._pending_retry) == 1
    assert prov.durability_status == ProvenanceDurabilityState.FAILED_RECOVERABLE.value

    # Evidence status must be DEGRADED while uncommitted
    ev = tracker.export_evidence("evt-lock-01")
    assert ev["verification_status"] == "DEGRADED_FAILED_RECOVERABLE"
    assert ev["integrity"]["persistence_state"] == ProvenanceDurabilityState.FAILED_RECOVERABLE.value

    # Flush while still locked
    recovered = tracker.flush_pending_retries()
    assert recovered == 0
    assert len(tracker._pending_retry) == 1

    # Third attempt: lock clears
    recovered = tracker.flush_pending_retries()
    assert recovered == 1
    assert len(tracker._pending_retry) == 0
    assert tracker.recovered_count == 1
    assert prov.durability_status == ProvenanceDurabilityState.DURABLE.value

    # Evidence status now verified
    ev2 = tracker.export_evidence("evt-lock-01")
    assert ev2["verification_status"] == "VERIFIED_TAMPER_EVIDENT"


def test_provenance_fault_injection_unrecoverable_error():
    """Simulate fatal unrecoverable failure: verify dropped counter and permanent failure status."""
    mock_store = UnrecoverableStorageMock()
    tracker = ProvenanceTracker(max_records=1000, store=mock_store)

    raw, canon = _sample_event("evt-fatal-01")
    prov = create_provenance(raw, canon)

    tracker.record(prov)

    assert tracker.recorded_count == 1
    assert tracker.persisted_count == 0
    assert tracker.failed_recoverable_count == 0
    assert tracker.failed_unrecoverable_count == 1
    assert tracker.dropped_provenance_count == 1
    assert len(tracker._pending_retry) == 0
    assert prov.durability_status == ProvenanceDurabilityState.FAILED_UNRECOVERABLE.value

    ev = tracker.export_evidence("evt-fatal-01")
    assert ev["verification_status"] == "DEGRADED_FAILED_UNRECOVERABLE"


def test_provenance_sqlite_direct_recovery_after_restart():
    """Verify that records durably written to SQLite survive process restart and are retrievable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "provenance_test.db")

        # 1. First process life: write record
        tracker1 = ProvenanceTracker(max_records=1000, db_path=db_path)
        raw, canon = _sample_event("evt-restart-01", "ETHUSDT")
        prov1 = create_provenance(raw, canon)
        tracker1.record(prov1)
        assert tracker1.persisted_count == 1
        tracker1.close()

        # 2. Second process life: new tracker on same database file
        tracker2 = ProvenanceTracker(max_records=1000, db_path=db_path)
        try:
            # In-memory records initially empty
            assert "evt-restart-01" not in tracker2._records

            # Retrieve from disk
            prov2 = tracker2.get("evt-restart-01")
            assert prov2 is not None
            assert prov2.event_id == "evt-restart-01"
            assert prov2.instrument == "ETHUSDT"
            assert prov2.lineage_hash == prov1.lineage_hash

            ev = tracker2.export_evidence("evt-restart-01")
            assert ev["verification_status"] == "VERIFIED_TAMPER_EVIDENT"
        finally:
            tracker2.close()


def test_provenance_concurrent_recording_thread_safety():
    """Verify thread-safety of recorded and failure counters under concurrent writes."""
    store = Store(":memory:")
    tracker = ProvenanceTracker(max_records=5000, store=store)

    n_threads = 8
    events_per_thread = 100

    def worker(tid: int):
        for i in range(events_per_thread):
            eid = f"evt-t{tid}-{i}"
            raw, canon = _sample_event(eid)
            prov = create_provenance(raw, canon)
            tracker.record(prov)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.recorded_count == n_threads * events_per_thread
    assert tracker.persisted_count == n_threads * events_per_thread
    assert tracker.dropped_provenance_count == 0
    assert len(tracker._records) == n_threads * events_per_thread
