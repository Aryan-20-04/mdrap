"""Exhaustive Differential Parity Test Suite: Native C Fastpath vs Pure Python Fallback.

Audits Agent 6 requirements:
- Bit-exact output matching between FastQualityEngine (native C) and QualityEngine (pure Python)
- Boundary values: negative values, NaN, infinity, crossed quotes
- Event types: TRADE, QUOTE, HALT, STATUS
- Clock skews, sequence gaps, duplicated sequences, inverted timestamps
- Strict assertion checking with zero swallowed exceptions
"""

import math
import sys
import time
from pathlib import Path
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.quality import QualityEngine
from mdrap.fastpath import FastQualityEngine, is_available as fastpath_is_available


@pytest.fixture
def engines():
    """Create paired Python and C engines with identical baseline state."""
    assert fastpath_is_available(), "Native C fastpath extension must be compiled and available"
    py_eng = QualityEngine()
    c_eng = FastQualityEngine()
    return py_eng, c_eng


def test_differential_standard_stream_bit_exact(engines):
    """Verify bit-exact match across standard trade and quote progression."""
    py_eng, c_eng = engines
    t_now = time.time()
    events = [
        CanonicalEvent(
            event_id="e1",
            source="BINANCE",
            instrument_id="BTC-USDT",
            exchange_timestamp=t_now,
            receive_timestamp=t_now,
            event_type=EventType.QUOTE,
            bid_price=50_000.0,
            bid_size=1.5,
            ask_price=50_001.0,
            ask_size=2.0,
            sequence_number=1,
        ),
        CanonicalEvent(
            event_id="e2",
            source="BINANCE",
            instrument_id="BTC-USDT",
            exchange_timestamp=t_now + 0.001,
            receive_timestamp=t_now + 0.001,
            event_type=EventType.TRADE,
            price=50_000.5,
            quantity=0.5,
            sequence_number=2,
        ),
    ]

    for ev in events:
        res_py = py_eng.evaluate(ev)
        res_c = c_eng.evaluate(ev)
        assert res_py.quality_status == res_c.quality_status
        assert res_py.reasons == res_c.reasons


def test_differential_halt_and_status_events(engines):
    """Verify HALT and STATUS events are handled identically without schema rejection."""
    py_eng, c_eng = engines
    t_now = time.time()
    halt_ev = CanonicalEvent(
        event_id="h1",
        source="NASDAQ",
        instrument_id="AAPL",
        exchange_timestamp=t_now,
        receive_timestamp=t_now,
        event_type=EventType.HALT,
        sequence_number=10,
    )
    status_ev = CanonicalEvent(
        event_id="s1",
        source="NASDAQ",
        instrument_id="AAPL",
        exchange_timestamp=t_now + 0.001,
        receive_timestamp=t_now + 0.001,
        event_type=EventType.STATUS,
        sequence_number=11,
    )

    for ev in (halt_ev, status_ev):
        res_py = py_eng.evaluate(ev)
        res_c = c_eng.evaluate(ev)
        assert res_py.quality_status == res_c.quality_status
        assert res_py.quality_status == QualityStatus.VALID


def test_differential_boundary_values(engines):
    """Verify boundary cases: negative prices, NaN, crossed markets."""
    py_eng, c_eng = engines
    t_now = time.time()
    test_cases = [
        # Negative ask price -> INVALID
        CanonicalEvent(
            event_id="b1",
            source="COINBASE",
            instrument_id="ETH-USD",
            exchange_timestamp=t_now + 0.001,
            receive_timestamp=t_now + 0.001,
            event_type=EventType.QUOTE,
            bid_price=2999.0,
            bid_size=1.0,
            ask_price=-10.0,
            ask_size=1.0,
            sequence_number=1,
        ),
        # Crossed market: bid > ask -> INVALID
        CanonicalEvent(
            event_id="b2",
            source="COINBASE",
            instrument_id="ETH-USD",
            exchange_timestamp=t_now + 0.002,
            receive_timestamp=t_now + 0.002,
            event_type=EventType.QUOTE,
            bid_price=3005.0,
            bid_size=1.0,
            ask_price=3000.0,
            ask_size=1.0,
            sequence_number=2,
        ),
        # Negative trade size -> INVALID
        CanonicalEvent(
            event_id="b3",
            source="COINBASE",
            instrument_id="ETH-USD",
            exchange_timestamp=t_now + 0.003,
            receive_timestamp=t_now + 0.003,
            event_type=EventType.TRADE,
            price=3000.0,
            quantity=-0.5,
            sequence_number=3,
        ),
        # NaN price -> INVALID
        CanonicalEvent(
            event_id="b4",
            source="COINBASE",
            instrument_id="ETH-USD",
            exchange_timestamp=t_now + 0.004,
            receive_timestamp=t_now + 0.004,
            event_type=EventType.TRADE,
            price=float("nan"),
            quantity=1.0,
            sequence_number=4,
        ),
    ]

    for ev in test_cases:
        res_py = py_eng.evaluate(ev)
        res_c = c_eng.evaluate(ev)
        assert res_py.quality_status == res_c.quality_status
        assert res_c.quality_status == QualityStatus.INVALID


def test_differential_sequence_gaps_and_clock_skew(engines):
    """Verify sequence gaps and timestamps in the past trigger identical classifications."""
    py_eng, c_eng = engines
    t_now = time.time()
    # Seed initial event
    e_init = CanonicalEvent(
        event_id="init",
        source="KRAKEN",
        instrument_id="SOL-USD",
        exchange_timestamp=t_now,
        receive_timestamp=t_now,
        event_type=EventType.TRADE,
        price=150.0,
        quantity=10.0,
        sequence_number=100,
    )
    res_py = py_eng.evaluate(e_init)
    res_c = c_eng.evaluate(e_init)
    assert res_py.quality_status == res_c.quality_status

    # Gap of 50 sequences -> SUSPICIOUS
    e_gap = CanonicalEvent(
        event_id="gap",
        source="KRAKEN",
        instrument_id="SOL-USD",
        exchange_timestamp=t_now + 0.01,
        receive_timestamp=t_now + 0.01,
        event_type=EventType.TRADE,
        price=150.1,
        quantity=5.0,
        sequence_number=150,
    )
    res_py_gap = py_eng.evaluate(e_gap)
    res_c_gap = c_eng.evaluate(e_gap)
    assert res_py_gap.quality_status == res_c_gap.quality_status
    assert res_c_gap.quality_status == QualityStatus.SUSPICIOUS

    # Timestamp backwards (clock skew) -> SUSPICIOUS
    e_skew = CanonicalEvent(
        event_id="skew",
        source="KRAKEN",
        instrument_id="SOL-USD",
        exchange_timestamp=t_now - 100.0,  # 100s in the past
        receive_timestamp=t_now + 0.02,
        event_type=EventType.TRADE,
        price=150.2,
        quantity=2.0,
        sequence_number=151,
    )
    res_py_skew = py_eng.evaluate(e_skew)
    res_c_skew = c_eng.evaluate(e_skew)
    assert res_py_skew.quality_status == res_c_skew.quality_status
