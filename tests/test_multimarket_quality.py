"""
Tests for Venue-Aware Market Quality & Microstructure Rules (Circuit Bands, Volatility Interruptions).
"""

import pytest
from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityEngine, QualityConfig


def _make_event(
    instrument="AAPL", price=100.0, venue="XNAS", currency="USD", seq=1, ts=1000.0
):
    return CanonicalEvent(
        event_id=f"evt-{seq}",
        instrument_id=instrument,
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=0.0,
        source="EXCHANGE_FEED",
        sequence_number=seq,
        price=price,
        quantity=100.0,
        venue=venue,
        currency=currency,
    )


def test_nse_circuit_filter_breach():
    """Verify Indian NSE price spike >= 10% is marked as CIRCUIT_FILTER_BREACH."""
    engine = QualityEngine(QualityConfig(price_anomaly_stddev=3.0, price_window=20))

    # Feed 20 baseline events at ₹2400.0
    for i in range(1, 21):
        evt = _make_event(
            instrument="RELIANCE.NS",
            price=2400.0 + (i % 2),
            venue="XNSE",
            currency="INR",
            seq=i,
            ts=1000.0 + i,
        )
        evaluated = engine.evaluate(evt)
        assert evaluated.quality_status == QualityStatus.VALID

    # Sudden 15% jump beyond circuit limit (₹2400 -> ₹2780)
    spike_evt = _make_event(
        instrument="RELIANCE.NS",
        price=2780.0,
        venue="XNSE",
        currency="INR",
        seq=21,
        ts=1022.0,
    )
    evaluated_spike = engine.evaluate(spike_evt)

    assert evaluated_spike.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.CIRCUIT_FILTER_BREACH.value in evaluated_spike.reasons


def test_xetra_volatility_interruption():
    """Verify German Xetra price corridor breach >= 5% is marked as VOLATILITY_INTERRUPTION."""
    engine = QualityEngine(QualityConfig(price_anomaly_stddev=3.0, price_window=20))

    # Feed 20 baseline events at €150.0
    for i in range(1, 21):
        evt = _make_event(
            instrument="SAP.DE",
            price=150.0 + 0.05 * (i % 2),
            venue="XETR",
            currency="EUR",
            seq=i,
            ts=1000.0 + i,
        )
        evaluated = engine.evaluate(evt)
        assert evaluated.quality_status == QualityStatus.VALID

    # Sudden 6% move beyond Xetra volatility corridor (€150 -> €160)
    spike_evt = _make_event(
        instrument="SAP.DE",
        price=160.0,
        venue="XETR",
        currency="EUR",
        seq=21,
        ts=1022.0,
    )
    evaluated_spike = engine.evaluate(spike_evt)

    assert evaluated_spike.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.VOLATILITY_INTERRUPTION.value in evaluated_spike.reasons


def test_tse_special_quote_indication():
    """Verify Japanese TSE Tokuhai special quote >= 8% is marked as SPECIAL_QUOTE_INDICATION."""
    engine = QualityEngine(QualityConfig(price_anomaly_stddev=3.0, price_window=20))

    # Feed 20 baseline events at ¥3000
    for i in range(1, 21):
        evt = _make_event(
            instrument="7203.T",
            price=3000.0 + (i % 2),
            venue="XTKS",
            currency="JPY",
            seq=i,
            ts=1000.0 + i,
        )
        evaluated = engine.evaluate(evt)
        assert evaluated.quality_status == QualityStatus.VALID

    # Sudden 10% move (¥3000 -> ¥3350)
    spike_evt = _make_event(
        instrument="7203.T",
        price=3350.0,
        venue="XTKS",
        currency="JPY",
        seq=21,
        ts=1022.0,
    )
    evaluated_spike = engine.evaluate(spike_evt)

    assert evaluated_spike.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SPECIAL_QUOTE_INDICATION.value in evaluated_spike.reasons


def test_us_standard_price_anomaly():
    """Verify standard US equities produce standard PRICE_ANOMALY."""
    engine = QualityEngine(QualityConfig(price_anomaly_stddev=3.0, price_window=20))

    # Feed baseline events at $100
    for i in range(1, 21):
        evt = _make_event(
            instrument="AAPL",
            price=100.0 + 0.01 * (i % 2),
            venue="XNAS",
            currency="USD",
            seq=i,
            ts=1000.0 + i,
        )
        engine.evaluate(evt)

    spike_evt = _make_event(
        instrument="AAPL", price=125.0, venue="XNAS", currency="USD", seq=21, ts=1022.0
    )
    evaluated = engine.evaluate(spike_evt)

    assert evaluated.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.PRICE_ANOMALY.value in evaluated.reasons
