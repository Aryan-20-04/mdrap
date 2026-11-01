"""Unit test suite for Phase 3: Market Data Correctness (E2, E3, E4, E5).

Verifies:
- E2: Stream recovers from corrupted large sequence jumps after normal sequence continuity resumes.
- E3: Historic retransmission (>64 events old) is correctly marked DUPLICATE (INVALID), not OUT_OF_ORDER.
- E4: Statistical price corridor reseeds after genuine market repricing (+20%) with intra-cluster variance.
- E5: Quote deduplication preserves quote size revisions at the same price and timestamp.
"""

import os
import shutil
import tempfile
import pytest

from mdrap.engine import Engine
from mdrap.models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
)
from mdrap.quality import QualityConfig, QualityEngine


def _make_event(
    event_id: str = "e1",
    instrument_id: str = "AAPL",
    event_type: EventType = EventType.TRADE,
    price: float = 150.0,
    quantity: float = 10.0,
    sequence: int | None = None,
    exchange_ts: float = 1000.0,
    **kwargs,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id=instrument_id,
        event_type=event_type,
        exchange_timestamp=exchange_ts,
        receive_timestamp=exchange_ts,
        price=price,
        quantity=quantity,
        sequence_number=sequence,
        **kwargs,
    )


def test_e2_fixed_sequence_recovery_from_large_jump():
    qe = QualityEngine()
    # Baseline
    qe.evaluate(_make_event(event_id="e1", sequence=10))
    # Huge jump
    qe.evaluate(_make_event(event_id="e2", sequence=9_000_000_000_000))
    qe.evaluate(_make_event(event_id="e3", sequence=9_000_000_000_001))

    # Resumed normal stream sequence
    qe.evaluate(_make_event(event_id="e4", sequence=11))
    res = qe.evaluate(_make_event(event_id="e5", sequence=12))

    # After 2 events, sequence state unpoisons and recovers continuity
    assert qe._slots[("", "AAPL")].last_seq == 12


def test_e3_fixed_old_retransmission_marked_duplicate():
    td = tempfile.mkdtemp(prefix="mdrap_e3_")
    try:
        engine = Engine(path=td)
        engine.submit(RawEvent(source="BINANCE", payload={"instrument": "BTC-USDT", "event_type": "TRADE", "price": 50000.0, "quantity": 1.0, "exchange_ts": 1000.0}))

        # Advance 70 events past 64-bit window
        for s in range(2, 75):
            engine.submit(RawEvent(source="BINANCE", payload={"instrument": "BTC-USDT", "event_type": "TRADE", "price": 50000.0 + s, "quantity": 1.0, "exchange_ts": 1000.0 + s}))

        # Re-transmit event 1
        dec = engine.submit(RawEvent(source="BINANCE", payload={"instrument": "BTC-USDT", "event_type": "TRADE", "price": 50000.0, "quantity": 1.0, "exchange_ts": 1000.0}))
        # Must be classified DUPLICATE and INVALID!
        assert Reason.DUPLICATE.value in dec.reasons
        assert dec.quality_status == QualityStatus.INVALID
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e4_fixed_price_corridor_reseeds_on_repricing():
    qe = QualityEngine()
    # Train at 100.0
    for i in range(100):
        qe.evaluate(_make_event(event_id=f"train_{i}", price=100.0, exchange_ts=1000.0 + i))

    # Re-rate at 120.0 with 1.5% tick volatility
    for i in range(60):
        px = 120.0 if (i % 2 == 0) else 121.8
        res = qe.evaluate(_make_event(event_id=f"rerate_{i}", price=px, exchange_ts=2000.0 + i))

    slot = qe._slots[("", "AAPL")]
    # Corridor has reseeded to the new regime!
    assert slot.mean > 115.0


def test_e5_fixed_quote_dedup_preserves_size_changes():
    td = tempfile.mkdtemp(prefix="test_e5_")
    try:
        engine = Engine(path=td)
        q1 = RawEvent(
            source="CME",
            payload={"instrument": "ES", "event_type": "QUOTE", "bid": 5000.0, "ask": 5001.0, "bid_size": 10.0, "ask_size": 20.0},
            receive_timestamp=1000.0
        )
        dec1 = engine.submit(q1)
        assert dec1.quality_status == QualityStatus.VALID

        # Same price, same ts, but bid_size changed to 50.0 (valid depth revision!)
        q2 = RawEvent(
            source="CME",
            payload={"instrument": "ES", "event_type": "QUOTE", "bid": 5000.0, "ask": 5001.0, "bid_size": 50.0, "ask_size": 20.0},
            receive_timestamp=1000.0
        )
        dec2 = engine.submit(q2)
        assert dec2.quality_status == QualityStatus.VALID
        assert Reason.DUPLICATE.value not in dec2.reasons
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)
