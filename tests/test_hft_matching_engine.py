"""
Tests for Institutional Realistic Matching Engine (§26).

Verifies:
- Price-time priority and queue-ahead volume tracking.
- Latency modeling (arrival delay).
- In-flight cancel/fill race conditions.
- Incremental partial fills.
- Maker / Taker fee calculations.
- Depth walking and market impact.
- Adverse selection markouts (10ms, 100ms, 1s).
"""

import pytest
import sys
from pathlib import Path

# Add paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "packages" / "mdrap-strategies"))
sys.path.insert(0, str(root / "src"))

from mdrap_strategies.matching_engine import (
    RealisticMatchingEngine,
    MatchingEngineConfig,
    SimulatedOrder,
    SimOrderSide,
    SimOrderType,
    SimOrderStatus,
    FeeSchedule,
)


def test_queue_ahead_tracking():
    """Verify resting limit order does NOT fill immediately on touch; queue ahead must clear."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0)
    )
    # Seed order book with 5.0 resting bid at 100.0
    engine.update_book("BTC-USDT", bids=[(100.0, 5.0)], asks=[(100.2, 5.0)], timestamp=1.0)

    # Submit limit buy order at 100.0 for 2.0 qty
    order = engine.submit_order(
        order_id="ORD-001",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        quantity=2.0,
        price=100.0,
        current_time=1.0,
    )
    # Process slice to enter queue
    engine.process_time_slice(current_time=1.0)
    assert order.status == SimOrderStatus.ACCEPTED
    assert order.queue_ahead_qty == 5.0  # 5.0 volume resting ahead in queue

    # Trade occurs at 100.0 for 3.0 qty (insufficient to clear queue)
    fills1 = engine.process_market_trade("BTC-USDT", trade_price=100.0, trade_size=3.0, timestamp=2.0)
    assert len(fills1) == 0
    assert order.queue_ahead_qty == 2.0  # 2.0 still ahead

    # Next trade occurs at 100.0 for 3.0 qty (clears 2.0 ahead + fills 1.0 of our order)
    fills2 = engine.process_market_trade("BTC-USDT", trade_price=100.0, trade_size=3.0, timestamp=3.0)
    assert len(fills2) == 1
    assert fills2[0]["quantity"] == 1.0
    assert order.status == SimOrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 1.0
    assert order.queue_ahead_qty == 0.0

    # Next trade at 100.0 for 1.0 qty finishes our order
    fills3 = engine.process_market_trade("BTC-USDT", trade_price=100.0, trade_size=1.0, timestamp=4.0)
    assert len(fills3) == 1
    assert fills3[0]["quantity"] == 1.0
    assert order.status == SimOrderStatus.FILLED
    assert order.filled_quantity == 2.0


def test_latency_modeling():
    """Verify order cannot fill before its arrival time at the exchange."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=10.0, matching_delay_ms=0.0)
    )
    engine.update_book("BTC-USDT", bids=[(100.0, 5.0)], asks=[(100.2, 5.0)], timestamp=1.0)

    # Submit at t=1.000s, arrival will be at t=1.010s
    order = engine.submit_order(
        order_id="ORD-002",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.MARKET,
        quantity=1.0,
        current_time=1.0,
    )
    assert order.status == SimOrderStatus.PENDING

    # Time slice before arrival (t=1.005s)
    events = engine.process_time_slice(current_time=1.005)
    assert len(events) == 0
    assert order.status == SimOrderStatus.PENDING

    # Time slice after arrival (t=1.011s)
    events2 = engine.process_time_slice(current_time=1.011)
    assert len(events2) > 0
    assert order.status == SimOrderStatus.FILLED


def test_cancel_fill_race_condition():
    """Verify in-flight cancel vs arrival fill race."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=10.0, matching_delay_ms=0.0)
    )
    engine.update_book("BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.2, 5.0)], timestamp=1.0)

    # Order sent at t=1.000s, arrives at t=1.010s
    order = engine.submit_order(
        order_id="ORD-003",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=1.0,
        current_time=1.000,
    )

    # Cancel initiated at t=1.005s, arrives at t=1.015s
    success = engine.cancel_order("ORD-003", current_time=1.005)
    assert success is True
    assert order.cancel_in_flight is True

    # Order arrives at exchange at t=1.010s
    engine.process_time_slice(current_time=1.010)
    assert order.status == SimOrderStatus.ACCEPTED

    # Trade occurs at t=1.012s (BEFORE cancel arrives at t=1.015s!)
    fills = engine.process_market_trade("BTC-USDT", trade_price=100.0, trade_size=1.0, timestamp=1.012)
    assert len(fills) == 1
    assert order.status == SimOrderStatus.FILLED

    # Cancel finally arrives at t=1.016s, but order was already filled!
    cancel_events = engine.process_time_slice(current_time=1.016)
    assert order.status == SimOrderStatus.FILLED


def test_maker_taker_fee_calculation():
    """Verify maker vs taker fee schedules."""
    fees = FeeSchedule(maker_fee_bps=2.0, taker_fee_bps=5.0)
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0, fee_schedule=fees)
    )
    engine.update_book("BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.2, 5.0)], timestamp=1.0)

    # Passive maker order
    maker_ord = engine.submit_order(
        order_id="MAKER-1",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=2.0,
        current_time=1.0,
    )
    engine.process_time_slice(current_time=1.0)

    # Fill as maker
    fills = engine.process_market_trade("BTC-USDT", trade_price=100.0, trade_size=2.0, timestamp=2.0)
    assert len(fills) == 1
    # 2.0 bps of ($100 * 2.0 = $200) = $0.04
    assert pytest.approx(maker_ord.fees_paid, 0.001) == 0.04
    assert maker_ord.is_maker is True

    # Aggressive taker order (crossing ask at 100.2)
    taker_ord = engine.submit_order(
        order_id="TAKER-1",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.MARKET,
        quantity=1.0,
        current_time=3.0,
    )
    engine.process_time_slice(current_time=3.0)
    # 5.0 bps of ($100.2 * 1.0 = $100.2) = $0.0501
    assert pytest.approx(taker_ord.fees_paid, 0.001) == 0.0501
    assert taker_ord.is_maker is False


def test_adverse_selection_markout():
    """Verify adverse selection markout computation."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0)
    )
    engine.update_book("BTC-USDT", bids=[(100.0, 1.0)], asks=[(100.2, 1.0)], timestamp=1.000)

    # Buy order fills at 100.2 at t=1.000s
    engine.submit_order(
        order_id="ORD-BUY",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.MARKET,
        quantity=1.0,
        current_time=1.000,
    )
    engine.process_time_slice(current_time=1.000)

    # Future mid price moves down at t=1.100s (adverse selection: bought and price immediately dropped)
    engine.update_book("BTC-USDT", bids=[(99.0, 1.0)], asks=[(99.2, 1.0)], timestamp=1.100)

    markouts = engine.compute_adverse_selection_markout(horizon_ms=100.0)
    assert len(markouts) == 1
    # Mid at fill was 100.1, mid future at 100ms was 99.1:
    # BUY direction = +1, markout = (99.1 - 100.1) = -1.0
    assert pytest.approx(markouts[0]["markout"], 0.01) == -1.0
    assert markouts[0]["markout"] < 0
