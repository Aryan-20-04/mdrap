import math
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityEngine, QualityConfig


def _make_event(
    source="FEED_A",
    instrument="AAPL",
    ev_type=EventType.TRADE,
    ex_ts=1000.0,
    rc_ts=1000.01,
    seq=1,
    price=150.0,
    qty=10.0,
    bid=None,
    ask=None,
):
    return CanonicalEvent(
        event_id=f"ev-{source}-{seq}",
        instrument_id=instrument,
        event_type=ev_type,
        exchange_timestamp=ex_ts,
        receive_timestamp=rc_ts,
        processing_timestamp=rc_ts,
        source=source,
        sequence_number=seq,
        price=price,
        quantity=qty,
        bid_price=bid,
        ask_price=ask,
    )


def test_q1_per_source_instrument_sequence_and_dedup():
    """Q1: Sequence tracking must be per (source, instrument), not global."""
    eng = QualityEngine()

    ev1 = _make_event(source="FEED_A", instrument="AAPL", seq=10)
    ev2 = _make_event(
        source="FEED_B", instrument="AAPL", seq=1
    )  # Lower sequence, but different source

    res1 = eng.evaluate(ev1)
    res2 = eng.evaluate(ev2)
    assert res1.quality_status == QualityStatus.VALID
    assert res2.quality_status == QualityStatus.VALID
    assert Reason.SEQUENCE_GAP.value not in res2.reasons
    assert Reason.OUT_OF_ORDER.value not in res2.reasons


def test_q2_clean_only_baseline():
    """Q2: Invalid prices must never be folded into the rolling baseline."""
    eng = QualityEngine()

    # Seed with 20 valid prices at 100.0
    for seq in range(1, 21):
        ev = _make_event(
            source="FEED_A",
            instrument="BTC/USD",
            ex_ts=1000.0 + seq,
            rc_ts=1000.0 + seq + 0.001,
            seq=seq,
            price=100.0,
        )
        eng.evaluate(ev)

    slot = eng._slots[("FEED_A", "BTC/USD")]
    assert slot.mean == pytest.approx(100.0)

    # An exact duplicate is INVALID
    dup_ev = _make_event(
        source="FEED_A",
        instrument="BTC/USD",
        ex_ts=1020.0,
        rc_ts=1020.001,
        seq=20,
        price=999.0,
    )
    res_dup = eng.evaluate(dup_ev)
    assert res_dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in res_dup.reasons

    # Verify baseline was NOT contaminated
    assert slot.mean == pytest.approx(100.0)


def test_q3_watermark_poisoning_guard():
    """Q3: Far-future timestamp must be flagged TS_IMPLAUSIBLE and NOT advance watermark."""
    eng = QualityEngine()

    # Normal tick
    ev1 = _make_event(
        source="FEED_A",
        instrument="AAPL",
        ex_ts=1000.0,
        rc_ts=1000.01,
        seq=1,
        price=150.0,
    )
    eng.evaluate(ev1)

    # Bogus future timestamp (exchange_ts = 2000.0 when receive_ts = 1000.02)
    ev_future = _make_event(
        source="FEED_A",
        instrument="AAPL",
        ex_ts=2000.0,
        rc_ts=1000.02,
        seq=2,
        price=150.0,
    )
    res_future = eng.evaluate(ev_future)
    assert res_future.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.TS_IMPLAUSIBLE.value in res_future.reasons

    # Normal subsequent tick at 1000.03 must NOT be flagged OUT_OF_ORDER
    ev3 = _make_event(
        source="FEED_A",
        instrument="AAPL",
        ex_ts=1000.03,
        rc_ts=1000.04,
        seq=3,
        price=150.0,
    )
    res3 = eng.evaluate(ev3)
    assert Reason.OUT_OF_ORDER.value not in res3.reasons


def test_q4_warmup_and_regime_shift():
    """Q4: Warm-up coarse jump test and regime-shift re-seed."""
    eng = QualityEngine()

    # During first few ticks (< 20 samples), small tick moves must NOT trigger price anomaly
    for seq in range(1, 10):
        px = 100.00 if seq % 2 == 0 else 100.05
        ev = _make_event(
            source="FEED_A",
            instrument="AAPL",
            ex_ts=1000.0 + seq,
            rc_ts=1000.0 + seq + 0.001,
            seq=seq,
            price=px,
        )
        res = eng.evaluate(ev)
        assert res.quality_status == QualityStatus.VALID
        assert Reason.PRICE_ANOMALY.value not in res.reasons

    # Fill up to 25 ticks to finish warm-up
    for seq in range(10, 26):
        ev = _make_event(
            source="FEED_A",
            instrument="AAPL",
            ex_ts=1000.0 + seq,
            rc_ts=1000.0 + seq + 0.001,
            seq=seq,
            price=100.0,
        )
        eng.evaluate(ev)

    # Now a regime shift: prices shift to 115.0 (15% jump)
    # The first few will be anomalous (SUSPICIOUS)
    for i in range(1, 9):
        seq = 25 + i
        ev = _make_event(
            source="FEED_A",
            instrument="AAPL",
            ex_ts=1000.0 + seq,
            rc_ts=1000.0 + seq + 0.001,
            seq=seq,
            price=115.0,
        )
        res = eng.evaluate(ev)
        if i < 8:
            assert res.quality_status == QualityStatus.SUSPICIOUS
            assert Reason.PRICE_ANOMALY.value in res.reasons

    # After 8 consecutive consistent anomalies, regime shift re-seeds!
    # Event 34 should now be VALID
    ev_post = _make_event(
        source="FEED_A",
        instrument="AAPL",
        ex_ts=1000.0 + 34,
        rc_ts=1000.0 + 34 + 0.001,
        seq=34,
        price=115.0,
    )
    res_post = eng.evaluate(ev_post)
    assert res_post.quality_status == QualityStatus.VALID
    assert Reason.PRICE_ANOMALY.value not in res_post.reasons
