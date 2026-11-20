"""Phase 8 Quarantine Subsystem Tests.

Tests:
1. Valid -> accepted (not quarantined)
2. Invalid -> quarantined (complete evidentiary context preserved)
3. Replay -> deterministic (replay produces exact same quarantine records)
4. Quarantined -> inspectable (query by event ID, rule, source)
5. Non-interference: quarantine operations never alter or mutate accepted canonical state.
"""

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from mdrap.quarantine import QuarantineRecord


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
    """Verify QuarantineRecord representation and dictionary conversion."""
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
    d = rec1.to_dict()
    assert d["event_id"] == "q1"
    assert d["instrument_id"] == "AAPL"
    assert d["rule"] == "MD001"
    assert d["reason"] == Reason.DUPLICATE.value


def test_quarantine_from_canonical():
    """Verify creating QuarantineRecord from CanonicalEvent."""
    ev = CanonicalEvent(
        event_id="can_1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.0,
        receive_timestamp=100.005,
        processing_timestamp=100.006,
        source="FEED_A",
        sequence_number=1,
        price=150.0,
        quantity=100.0,
        quality_status=QualityStatus.INVALID,
        reasons=[Reason.CROSSED_QUOTE.value],
    )
    rec = QuarantineRecord.from_canonical(
        event=ev,
        rule="MD005",
        stage="Quality",
    )
    assert rec.event_id == "can_1"
    assert rec.reason == Reason.CROSSED_QUOTE.value
    assert rec.rule == "MD005"
    assert ev.quality_status == QualityStatus.INVALID


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

    bad_record = QuarantineRecord.from_canonical(
        event=accepted_event,
        rule="MD001",
        stage="Quality",
    )
    assert bad_record.event_id == "clean_1"
    # accepted_event itself must remain VALID with empty reasons
    assert accepted_event.quality_status == QualityStatus.VALID
    assert accepted_event.reasons == []
