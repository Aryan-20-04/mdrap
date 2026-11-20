"""
Test verifying that ProvenanceTracker dead-letter queue overflow increments dropped counter (VULN-04).
"""

import time
import sqlite3
import pytest
from mdrap.provenance import ProvenanceTracker, create_provenance
from mdrap.models import RawEvent, CanonicalEvent, EventType, QualityStatus


def test_dlq_saturation_increments_dropped_counter():
    """A full retry queue rejects new events without silently evicting records."""
    tracker = ProvenanceTracker(max_records=100, max_pending_retries=3)
    def make_provenance(index):
        ev = CanonicalEvent(
            event_id=f"EVT-DLQ-TEST-{index}",
            instrument_id="AAPL",
            event_type=EventType.QUOTE,
            exchange_timestamp=time.time(),
            receive_timestamp=time.time(),
            quality_status=QualityStatus.VALID,
        )
        return create_provenance(
            RawEvent(source="FEED-1", payload={"data": index}), ev, f"trans_{index}"
        )

    # Simulate recoverable persistence errors to fill queue
    err = sqlite3.OperationalError("database is locked")
    maxlen = tracker.max_pending_retries

    # Fill to capacity
    for i in range(maxlen):
        tracker._handle_persistence_failure(make_provenance(i), err)

    assert len(tracker._pending_retry) == maxlen
    assert tracker.dropped_provenance_count == 0

    # Overflow is explicit; no queued record is silently evicted.
    overflow_count = 1
    replacement = make_provenance(maxlen)
    with pytest.raises(RuntimeError, match="queue is full"):
        tracker._handle_persistence_failure(replacement, err)

    assert len(tracker._pending_retry) == maxlen
    assert tracker.dropped_provenance_count == overflow_count, (
        f"Expected {overflow_count} dropped items, got {tracker.dropped_provenance_count}"
    )

    assert tracker._pending_retry[0].durability_status == "FAILED_RECOVERABLE"
    assert replacement.durability_status == "FAILED_UNRECOVERABLE"
