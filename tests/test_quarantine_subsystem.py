"""Phase 8 Quarantine Subsystem Tests.

Tests:
1. Valid -> accepted (not quarantined)
2. Invalid -> quarantined (complete evidentiary context preserved)
3. Replay -> deterministic (replay produces exact same quarantine records)
4. Quarantined -> inspectable (query by event ID, rule, source)
5. Non-interference: quarantine operations never alter or mutate accepted canonical state.
"""

from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from quarantine import QuarantineManager, QuarantineRecord


def test_quarantine_record_preservation():
    """Verify all 7 required context fields are preserved in QuarantineRecord."""
    raw = RawEvent(
        raw_id="raw_fail_1",
        source="FEED_X",
        receive_timestamp=100.50,
        payload={"instrument": "TSLA", "bad_field": "corrupt"},
    )
    record = QuarantineRecord.from_raw(
        raw=raw,
        rule="MD007",
        reason="SCHEMA_VIOLATION",
        stage="Gateway",
        metadata={"error_detail": "Missing trade price"},
    )

    assert record.event_id == "raw_fail_1"
    assert record.instrument_id == "TSLA"
    assert record.source == "FEED_X"
    assert record.timestamp == 100.50
    assert record.rule == "MD007"
    assert record.reason == "SCHEMA_VIOLATION"
    assert record.stage == "Gateway"
    assert record.raw_payload == {"instrument": "TSLA", "bad_field": "corrupt"}
    assert record.metadata["error_detail"] == "Missing trade price"


def test_quarantine_isolation_and_inspection():
    """Quarantine manager stores, queries, and filters rejected records."""
    qm = QuarantineManager()

    rec1 = QuarantineRecord(
        event_id="q1",
        instrument_id="AAPL",
        source="FEED_A",
        timestamp=100.0,
        rule="MD001",
        reason=Reason.DUPLICATE.value,
        stage="Quality",
        raw_payload={"price": 150.0},
    )
    rec2 = QuarantineRecord(
        event_id="q2",
        instrument_id="MSFT",
        source="FEED_B",
        timestamp=101.0,
        rule="MD007",
        reason=Reason.SCHEMA_VIOLATION.value,
        stage="Gateway",
        raw_payload={"price": "not_a_number"},
    )

    qm.isolate(rec1)
    qm.isolate(rec2)

    assert qm.count() == 2
    assert qm.get("q1") == rec1
    assert qm.get("q2") == rec2

    # Filter by rule
    dups = qm.list_records(rule="MD001")
    assert len(dups) == 1
    assert dups[0].event_id == "q1"

    # Filter by source
    feed_b = qm.list_records(source="FEED_B")
    assert len(feed_b) == 1
    assert feed_b[0].event_id == "q2"


def test_quarantine_deterministic_replay():
    """Replaying an identical stream produces an identical quarantine ledger."""

    def run_stream():
        qm = QuarantineManager()
        for i in range(50):
            if i % 5 == 0:
                qm.isolate(
                    QuarantineRecord(
                        event_id=f"bad_{i}",
                        instrument_id="AAPL",
                        source="FEED_A",
                        timestamp=100.0 + i,
                        rule="MD005",
                        reason=Reason.CROSSED_QUOTE.value,
                        stage="Book",
                        raw_payload={"bid": 150.0, "ask": 149.0},
                    )
                )
        return [(r.event_id, r.rule, r.reason) for r in qm.list_records(limit=100)]

    run1 = run_stream()
    run2 = run_stream()
    assert len(run1) == 10
    assert run1 == run2


def test_quarantine_does_not_mutate_accepted_data():
    """Isolating an event in quarantine leaves accepted canonical objects untouched."""
    accepted_event = CanonicalEvent(
        event_id="clean_1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.0,
        receive_timestamp=100.005,
        processing_timestamp=100.006,
        source="FEED_A",
        sequence_number=1,
        venue="NASDAQ",
        price=150.0,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
    )

    qm = QuarantineManager()
    bad_record = QuarantineRecord.from_canonical(
        event=accepted_event,
        rule="MD001",
        stage="Quality",
    )
    qm.isolate(bad_record)

    # accepted_event itself must remain VALID with empty reasons
    assert accepted_event.quality_status == QualityStatus.VALID
    assert accepted_event.reasons == []
    assert qm.get("clean_1") is not None
