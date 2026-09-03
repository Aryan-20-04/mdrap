import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityConfig, QualityEngine


def make_event(**overrides):
    base = dict(
        event_id="e1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=0.0, source="FEEDX", sequence_number=1,
        price=100.0, quantity=10.0,
    )
    base.update(overrides)
    return CanonicalEvent(**base)


def test_first_event_is_valid():
    qe = QualityEngine()
    e = qe.evaluate(make_event())
    assert e.quality_status == QualityStatus.VALID


def test_exact_duplicate_is_invalid():
    qe = QualityEngine()
    qe.evaluate(make_event(event_id="e1"))
    dup = qe.evaluate(make_event(event_id="e2"))  # same source/instrument/sequence
    assert dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in dup.reasons


def test_sequence_gap_is_suspicious_not_invalid():
    qe = QualityEngine()
    qe.evaluate(make_event(sequence_number=1))
    e = qe.evaluate(make_event(event_id="e2", sequence_number=5))
    assert e.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in e.reasons


def test_out_of_order_detected():
    qe = QualityEngine()
    qe.evaluate(make_event(sequence_number=1, exchange_timestamp=1000.0))
    e = qe.evaluate(make_event(event_id="e2", sequence_number=2, exchange_timestamp=999.0))
    assert Reason.OUT_OF_ORDER.value in e.reasons


def test_staleness_detected():
    qe = QualityEngine(QualityConfig(staleness_threshold_s=0.01))
    e = qe.evaluate(make_event(exchange_timestamp=1000.0, receive_timestamp=1000.5))
    assert Reason.STALE.value in e.reasons


def test_crossed_quote_is_invalid():
    qe = QualityEngine()
    e = qe.evaluate(make_event(
        event_type=EventType.QUOTE, price=None, quantity=None,
        bid_price=101.0, ask_price=100.0,
    ))
    assert e.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in e.reasons


def test_price_anomaly_flagged_after_stable_baseline():
    qe = QualityEngine(QualityConfig(price_anomaly_stddev=3.0, price_window=20))
    # Build a stable baseline with tiny jitter.
    for i in range(20):
        qe.evaluate(make_event(event_id=f"b{i}", sequence_number=i + 1, price=100.0 + (i % 2) * 0.01))
    spike = qe.evaluate(make_event(event_id="spike", sequence_number=21, price=150.0))
    assert Reason.PRICE_ANOMALY.value in spike.reasons
    assert spike.quality_status == QualityStatus.SUSPICIOUS  # anomaly, not auto-invalid


def test_price_anomaly_is_per_source_not_shared_across_feeds():
    """Two sources with different (but each internally stable) price
    levels for the same instrument should not trip each other's
    anomaly detector -- price sanity must be scoped per (source, instrument).
    """
    qe = QualityEngine(QualityConfig(price_anomaly_stddev=3.0, price_window=20))
    for i in range(20):
        qe.evaluate(make_event(event_id=f"x{i}", source="FEEDX", sequence_number=i + 1, price=100.0))
        qe.evaluate(make_event(event_id=f"y{i}", source="FEEDY", sequence_number=i + 1, price=200.0))
    e = qe.evaluate(make_event(event_id="z", source="FEEDX", sequence_number=21, price=100.5))
    assert Reason.PRICE_ANOMALY.value not in e.reasons


def test_schema_violation_routes_through_normalize():
    import pytest
    from gateway import SchemaError, normalize
    from models import RawEvent
    raw = RawEvent(source="FEEDX", payload={"event_type": "TRADE"})  # missing required fields
    with pytest.raises(SchemaError):
        normalize(raw)
