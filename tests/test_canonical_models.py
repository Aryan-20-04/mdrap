"""
Tests for Canonical Data Models & Specialized Subtypes (Phase 5).

Verifies:
- Serialization & Deserialization (to_dict, from_dict, to_json, from_json)
- Timestamp semantics (nanosecond conversion, boundary limits)
- Sequence semantics (rollover handling, 64-bit uint range)
- Precision preservation across floats
- Extreme & boundary values (infinity, NaN, zero, negatives)
- Missing & malformed fields handled safely without unhandled exceptions
"""

import math
import time
import pytest
from models import (
    AssetClass,
    BookEvent,
    CanonicalEvent,
    DepthEvent,
    EventType,
    MarketEvent,
    QualityStatus,
    QuoteEvent,
    RawEvent,
    Reason,
    TradeEvent,
    safe_parse_market_event,
)


def test_canonical_event_serialization_roundtrip():
    """Verify CanonicalEvent roundtrips cleanly through to_dict and from_dict."""
    ev = CanonicalEvent(
        event_id="EV_12345",
        instrument_id="BTC-USDT",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.123456,
        receive_timestamp=1700000000.123500,
        processing_timestamp=1700000000.123600,
        source="BINANCE",
        sequence_number=987654321,
        price=50000.25,
        quantity=1.5,
        venue="BINANCE",
        currency="USDT",
        quality_status=QualityStatus.VALID,
        reasons=[],
    )

    d = ev.to_dict()
    assert d["event_id"] == "EV_12345"
    assert d["price"] == 50000.25
    assert d["quantity"] == 1.5
    assert d["event_type"] == "TRADE"

    json_str = ev.to_json()
    reconstructed = CanonicalEvent.from_json(json_str)

    assert reconstructed.event_id == ev.event_id
    assert reconstructed.instrument_id == ev.instrument_id
    assert reconstructed.event_type == EventType.TRADE
    assert abs(reconstructed.exchange_timestamp - ev.exchange_timestamp) < 1e-9
    assert reconstructed.price == ev.price
    assert reconstructed.quantity == ev.quantity
    assert reconstructed.sequence_number == ev.sequence_number
    assert reconstructed.quality_status == QualityStatus.VALID


def test_trade_event_specialization_and_timestamps():
    """Verify TradeEvent model, properties, and nanosecond timestamp calculations."""
    now = time.time()
    trade = TradeEvent(
        event_id="TR_1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=now,
        receive_timestamp=now + 0.001,
        source="NASDAQ",
        sequence_number=1001,
        price=185.50,
        quantity=500.0,
        side="BUY",
        trade_id="MATCH_999",
    )

    assert trade.exchange_timestamp_ns == int(now * 1_000_000_000)
    assert trade.receive_timestamp_ns == int((now + 0.001) * 1_000_000_000)
    assert trade.price == 185.50
    assert trade.side == "BUY"

    d = trade.to_dict()
    assert d["side"] == "BUY"
    assert d["trade_id"] == "MATCH_999"


def test_quote_event_spread_and_mid():
    """Verify QuoteEvent spread and midpoint computations."""
    quote = QuoteEvent(
        event_id="Q_1",
        instrument_id="NVDA",
        event_type=EventType.QUOTE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        source="BATS",
        bid_price=450.00,
        bid_size=1000.0,
        ask_price=450.10,
        ask_size=800.0,
    )

    assert math.isclose(quote.spread, 0.10, abs_tol=1e-6)
    assert math.isclose(quote.mid_price, 450.05, abs_tol=1e-6)

    d = quote.to_dict()
    assert math.isclose(d["spread"], 0.10, abs_tol=1e-6)
    assert math.isclose(d["mid_price"], 450.05, abs_tol=1e-6)


def test_sequence_number_uint64_rollover():
    """Verify 64-bit unsigned integer sequence numbers roll over cleanly."""
    max_uint64 = 2**64 - 1
    rolled_over = 2**64 + 42

    data = {
        "symbol": "ETH/USD",
        "source": "COINBASE",
        "price": 3200.0,
        "quantity": 10.0,
        "seq": rolled_over,
    }

    ev, errs = safe_parse_market_event(data)
    assert ev is not None
    assert ev.sequence_number == 42


def test_safe_parse_handles_missing_fields_without_crash():
    """Verify parser safely identifies missing fields and returns INVALID status without crashing."""
    payload = {}
    ev, errs = safe_parse_market_event(payload)
    assert ev is not None
    assert ev.quality_status == QualityStatus.INVALID
    assert any("instrument_id" in err for err in errs)


def test_safe_parse_handles_extreme_and_corrupt_types():
    """Verify non-finite numbers (NaN, Inf) and corrupt types are sanitized gracefully."""
    payload = {
        "instrument_id": "SPY",
        "source": "CBOE",
        "price": float("nan"),
        "quantity": float("inf"),
        "exchange_timestamp": "not_a_float",
    }

    ev, errs = safe_parse_market_event(payload)
    assert ev is not None
    assert isinstance(ev, TradeEvent)
    assert ev.quality_status == QualityStatus.INVALID
    assert ev.price == 0.0
    assert ev.quantity == 0.0
    assert any("exchange_timestamp" in err for err in errs)


def test_depth_event_level2_parsing():
    """Verify Level 2 DepthEvent with multiple bid and ask price ladders."""
    payload = {
        "instrument_id": "BTC/USD",
        "type": "DEPTH",
        "source": "KRAKEN",
        "bids": [[50000.0, 1.2], [49990.0, 3.4], ["invalid", "corrupt"]],
        "asks": [[50010.0, 2.0], [50020.0, 5.1]],
        "is_snapshot": True,
    }

    ev, errs = safe_parse_market_event(payload)
    assert isinstance(ev, DepthEvent)
    assert len(ev.bids) == 2  # The corrupt pair is filtered safely
    assert ev.bids[0] == (50000.0, 1.2)
    assert len(ev.asks) == 2
    assert ev.is_snapshot is True
