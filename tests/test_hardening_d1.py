import os
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from models import CanonicalEvent, EventType, QualityStatus
from storage import Store


def test_d1_evidence_tables_never_overwritten():
    store = Store(":memory:")
    
    ev1 = CanonicalEvent(
        event_id="evt-100",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDX",
        sequence_number=1,
        price=150.0,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
        raw_id="raw-1",
    )
    store.write_canonical_batch([ev1])
    store.commit()
    
    # Tamper attempt: insert same event_id with different price
    ev1_tampered = CanonicalEvent(
        event_id="evt-100",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDX",
        sequence_number=1,
        price=9999.99,  # Forged price
        quantity=100.0,
        quality_status=QualityStatus.VALID,
        raw_id="raw-1",
    )
    store.write_canonical_batch([ev1_tampered])
    store.commit()
    
    # Query must return original price 150.0, NOT 9999.99
    rows = store.query_events(instrument_id="AAPL")
    assert len(rows) == 1
    assert rows[0]["price"] == 150.0
    assert getattr(store, "conflicts", 0) >= 1
    
    # Quarantine immutability check
    q_orig = ("q-1", "AAPL", "FEEDX", "INVALID", "[]", "original_payload", 1000.0)
    q_tampered = ("q-1", "AAPL", "FEEDX", "INVALID", "[]", "TAMPERED_payload", 1000.0)
    store.write_quarantine_batch([q_orig])
    store.write_quarantine_batch([q_tampered])
    store.commit()
    
    q_rows = store.query_quarantine()
    assert len(q_rows) == 1
    assert q_rows[0]["payload_json"] == "original_payload"
