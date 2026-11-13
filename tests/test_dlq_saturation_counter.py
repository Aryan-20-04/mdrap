"""
Test verifying that ProvenanceTracker dead-letter queue overflow increments dropped counter (VULN-04).
"""

import time
import sqlite3
from mdrap.provenance import ProvenanceTracker, create_provenance
from mdrap.models import RawEvent, CanonicalEvent, EventType, QualityStatus


def test_dlq_saturation_increments_dropped_counter():
    """Verify that saturating _pending_retry increments dropped_provenance_count on eviction."""
    tracker = ProvenanceTracker(max_records=100)
    tracker._pending_retry.clear()
    tracker.dropped_provenance_count = 0

    ev = CanonicalEvent(
        event_id="EVT-DLQ-TEST",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=time.time(),
        receive_timestamp=time.time(),
        quality_status=QualityStatus.VALID,
    )
    raw = RawEvent(source="FEED-1", payload={"data": 1})
    prov = create_provenance(raw, ev, "trans_1")

    # Simulate recoverable persistence errors to fill queue
    err = sqlite3.OperationalError("database is locked")
    maxlen = tracker._pending_retry.maxlen

    # Fill to capacity
    for _ in range(maxlen):
        tracker._handle_persistence_failure(prov, err)

    assert len(tracker._pending_retry) == maxlen
    assert tracker.dropped_provenance_count == 0

    # Overflow by 25 items
    overflow_count = 25
    for _ in range(overflow_count):
        tracker._handle_persistence_failure(prov, err)

    assert len(tracker._pending_retry) == maxlen
    assert tracker.dropped_provenance_count == overflow_count, (
        f"Expected {overflow_count} dropped items, got {tracker.dropped_provenance_count}"
    )

    evicted = tracker._pending_retry[0]
    replacement_event = CanonicalEvent(
        event_id="EVT-DLQ-REPLACEMENT",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=time.time(),
        receive_timestamp=time.time(),
        quality_status=QualityStatus.VALID,
    )
    replacement = create_provenance(
        RawEvent(source="FEED-1", payload={"data": 2}), replacement_event, "trans_2"
    )
    tracker._handle_persistence_failure(replacement, err)
    assert evicted.durability_status == "FAILED_UNRECOVERABLE"
    assert tracker.dropped_provenance_count == overflow_count + 1
