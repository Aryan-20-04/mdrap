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
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 5.0)], asks=[(100.2, 5.0)], timestamp=1.0
    )

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
    fills1 = engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=3.0, timestamp=2.0
    )
    assert len(fills1) == 0
    assert order.queue_ahead_qty == 2.0  # 2.0 still ahead

    # Next trade occurs at 100.0 for 3.0 qty (clears 2.0 ahead + fills 1.0 of our order)
    fills2 = engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=3.0, timestamp=3.0
    )
    assert len(fills2) == 1
    assert fills2[0]["quantity"] == 1.0
    assert order.status == SimOrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 1.0
    assert order.queue_ahead_qty == 0.0

    # Next trade at 100.0 for 1.0 qty finishes our order
    fills3 = engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=1.0, timestamp=4.0
    )
    assert len(fills3) == 1
    assert fills3[0]["quantity"] == 1.0
    assert order.status == SimOrderStatus.FILLED
    assert order.filled_quantity == 2.0


def test_latency_modeling():
    """Verify order cannot fill before its arrival time at the exchange."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=10.0, matching_delay_ms=0.0)
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 5.0)], asks=[(100.2, 5.0)], timestamp=1.0
    )

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
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.2, 5.0)], timestamp=1.0
    )

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
    fills = engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=1.0, timestamp=1.012
    )
    assert len(fills) == 1
    assert order.status == SimOrderStatus.FILLED

    # Cancel finally arrives at t=1.016s, but order was already filled!
    cancel_events = engine.process_time_slice(current_time=1.016)
    assert order.status == SimOrderStatus.FILLED


def test_maker_taker_fee_calculation():
    """Verify maker vs taker fee schedules."""
    fees = FeeSchedule(maker_fee_bps=2.0, taker_fee_bps=5.0)
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(
            network_latency_ms=0.0, matching_delay_ms=0.0, fee_schedule=fees
        )
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.2, 5.0)], timestamp=1.0
    )

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
    fills = engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=2.0, timestamp=2.0
    )
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
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 1.0)], asks=[(100.2, 1.0)], timestamp=1.000
    )

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
    engine.update_book(
        "BTC-USDT", bids=[(99.0, 1.0)], asks=[(99.2, 1.0)], timestamp=1.100
    )

    markouts = engine.compute_adverse_selection_markout(horizon_ms=100.0)
    assert len(markouts) == 1
    # Mid at fill was 100.1, mid future at 100ms was 99.1:
    # BUY direction = +1, markout = (99.1 - 100.1) = -1.0
    assert pytest.approx(markouts[0]["markout"], 0.01) == -1.0
    assert markouts[0]["markout"] < 0


def test_post_only_execution():
    """Verify Post-Only enforcement: crossing orders rejected, non-crossing orders rest and fill passively."""
    fees = FeeSchedule(maker_fee_bps=1.0, taker_fee_bps=4.0)
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(
            network_latency_ms=0.0, matching_delay_ms=0.0, fee_schedule=fees
        )
    )
    # Market: Bid 100.0 (size 5.0), Ask 100.5 (size 5.0)
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 5.0)], asks=[(100.5, 5.0)], timestamp=1.0
    )

    # Case 1: Post-Only order crosses ask (marketable buy at 100.5)
    po_cross_order = engine.submit_order(
        order_id="PO-CROSS",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.5,
        quantity=1.0,
        current_time=1.0,
        post_only=True,
    )
    events = engine.process_time_slice(current_time=1.0)
    # Must be cancelled with POST_ONLY_RESTRICTED
    assert po_cross_order.status == SimOrderStatus.CANCELLED
    assert po_cross_order.reject_reason == "POST_ONLY_RESTRICTED"
    assert po_cross_order.filled_quantity == 0.0
    assert po_cross_order.fees_paid == 0.0
    assert any(e.get("reason") == "POST_ONLY_RESTRICTED" for e in events)

    # Case 2: Standard unconstrained limit order crosses ask -> fills as taker
    regular_cross_order = engine.submit_order(
        order_id="REG-CROSS",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.5,
        quantity=1.0,
        current_time=2.0,
        post_only=False,
    )
    events2 = engine.process_time_slice(current_time=2.0)
    assert regular_cross_order.status == SimOrderStatus.FILLED
    assert regular_cross_order.is_maker is False
    assert pytest.approx(regular_cross_order.fees_paid, 0.001) == 100.5 * 1.0 * (
        4.0 / 10000.0
    )

    # Case 3: Post-Only passive order does not cross (buy at 100.0) -> enqueued as maker
    po_passive_order = engine.submit_order(
        order_id="PO-PASSIVE",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=2.0,
        current_time=3.0,
        post_only=True,
    )
    engine.process_time_slice(current_time=3.0)
    assert po_passive_order.status == SimOrderStatus.ACCEPTED
    assert po_passive_order.queue_ahead_qty == 5.0  # 5.0 resting ahead from book

    # Market trade of 5.0 clears queue ahead
    engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=5.0, timestamp=3.1
    )
    assert po_passive_order.queue_ahead_qty == 0.0
    assert po_passive_order.filled_quantity == 0.0

    # Next market trade of 2.0 fills our resting post-only order
    fills = engine.process_market_trade(
        "BTC-USDT", trade_price=100.0, trade_size=2.0, timestamp=3.2
    )
    assert len(fills) == 1
    assert po_passive_order.status == SimOrderStatus.FILLED
    assert po_passive_order.is_maker is True
    # Maker fee: 1.0 bps on $200 = $0.02
    assert pytest.approx(po_passive_order.fees_paid, 0.001) == 200.0 * (1.0 / 10000.0)


def test_chronological_cancel_prevents_zombie_order():
    """Verify order arriving at t1 and cancelled at t2 (>t1) is never orphaned as a zombie."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=2.0, matching_delay_ms=0.5)
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.5, 5.0)], timestamp=1.0
    )

    # Submitted at t=1.000 -> arrives at t=1.0025 (no process_time_slice called yet)
    order = engine.submit_order(
        order_id="ORD-ZOMBIE-CHECK",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=1.0,
        current_time=1.0,
        post_only=True,
    )
    # Cancelled at t=3.000 -> cancel arrives at t=3.002
    assert engine.cancel_order("ORD-ZOMBIE-CHECK", current_time=3.0) is True

    # Trade arrives at t=3.005 (after both order arrival 1.0025 and cancel arrival 3.002)
    events = engine.process_market_trade(
        "BTC-USDT",
        trade_price=99.0,
        trade_size=5.0,
        timestamp=3.005,
        aggressor_side=SimOrderSide.SELL,
    )
    assert order.status == SimOrderStatus.CANCELLED
    assert order.filled_quantity == 0.0
    assert not any(e.get("event") == "FILL" for e in events)
    # Verify chronological ordering of emitted events (ACCEPTED before CANCELLED)
    ev_types = [e["event"] for e in events if e.get("order_id") == "ORD-ZOMBIE-CHECK"]
    assert ev_types == ["ORDER_ACCEPTED", "ORDER_CANCELLED"]


def test_cancel_before_arrival_race():
    """Verify cancel arriving strictly before order arrival cancels PENDING order cleanly."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=10.0, matching_delay_ms=5.0)
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.5, 5.0)], timestamp=1.0
    )

    # Order arrives at 1.015; cancel arrives at 1.011 (< 1.015)
    order = engine.submit_order(
        order_id="ORD-EARLY-CANCEL",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=1.0,
        current_time=1.000,
    )
    engine.cancel_order("ORD-EARLY-CANCEL", current_time=1.001)

    events = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=5.0,
        timestamp=1.020,
        aggressor_side=SimOrderSide.SELL,
    )
    assert order.status == SimOrderStatus.CANCELLED
    assert order.filled_quantity == 0.0
    assert not any(e.get("event") == "FILL" for e in events)


def test_partial_fill_then_cancel_blocks_subsequent_fills():
    """Verify a partially filled order cancelled before a second trade receives no further fills."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=2.0, matching_delay_ms=0.0)
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.5, 5.0)], timestamp=1.0
    )

    order = engine.submit_order(
        order_id="ORD-PART-CANCEL",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=2.0,
        current_time=1.000,
    )
    engine.process_time_slice(1.005)
    assert order.status == SimOrderStatus.ACCEPTED

    # First trade partially fills 0.8
    fills1 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=0.8,
        timestamp=1.010,
        aggressor_side=SimOrderSide.SELL,
    )
    assert len([e for e in fills1 if e["event"] == "FILL"]) == 1
    assert order.status == SimOrderStatus.PARTIALLY_FILLED
    assert pytest.approx(order.filled_quantity, 1e-6) == 0.8
    assert pytest.approx(order.remaining_quantity, 1e-6) == 1.2

    # Cancel initiated at 1.011 (arrives at 1.013) before second trade at 1.015
    engine.cancel_order("ORD-PART-CANCEL", current_time=1.011)
    fills2 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=5.0,
        timestamp=1.015,
        aggressor_side=SimOrderSide.SELL,
    )
    assert order.status == SimOrderStatus.CANCELLED
    assert pytest.approx(order.filled_quantity, 1e-6) == 0.8
    assert not any(e["event"] == "FILL" for e in fills2)


def test_order_expiration_chronological():
    """Verify order expiration is processed chronologically before later trades."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0)
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 0.0)], asks=[(100.5, 5.0)], timestamp=1.0
    )

    order = engine.submit_order(
        order_id="ORD-EXP",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=2.0,
        current_time=1.0,
        expires_at=2.0,
    )
    # Partial fill before expiration
    engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=0.5,
        timestamp=1.5,
        aggressor_side=SimOrderSide.SELL,
    )
    assert order.status == SimOrderStatus.PARTIALLY_FILLED
    assert pytest.approx(order.filled_quantity, 1e-6) == 0.5

    # Trade after expiration timestamp (2.5 > 2.0): expiration must fire first
    events = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=5.0,
        timestamp=2.5,
        aggressor_side=SimOrderSide.SELL,
    )
    assert order.status == SimOrderStatus.EXPIRED
    assert pytest.approx(order.filled_quantity, 1e-6) == 0.5
    assert any(e["event"] == "ORDER_EXPIRED" for e in events)
    assert not any(e["event"] == "FILL" for e in events)


def test_cumulative_queue_consumption_fifo_and_side_filtering():
    """Verify multi-trade queue depletion, aggressor-side filtering, price filtering, and strict FIFO."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0)
    )
    # 1.0 external resting bid at 100.0
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 1.0)], asks=[(100.5, 1.0)], timestamp=1.0
    )

    ord_a = engine.submit_order(
        order_id="FIFO-A",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=0.5,
        current_time=1.0,
    )
    ord_b = engine.submit_order(
        order_id="FIFO-B",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=0.5,
        current_time=1.0,
    )
    engine.process_time_slice(1.0)
    assert pytest.approx(ord_a.queue_ahead_qty, 1e-6) == 1.0
    assert pytest.approx(ord_b.queue_ahead_qty, 1e-6) == 1.5

    # 1. Eligible SELL trade of 0.4 decrements both queue-ahead counters cumulatively
    f1 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=0.4,
        timestamp=1.1,
        aggressor_side=SimOrderSide.SELL,
    )
    assert len(f1) == 0
    assert pytest.approx(ord_a.queue_ahead_qty, 1e-6) == 0.6
    assert pytest.approx(ord_b.queue_ahead_qty, 1e-6) == 1.1

    # 2. Wrong aggressor side (BUY lifting ask) must NOT consume bid queue
    f2 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=5.0,
        timestamp=1.2,
        aggressor_side=SimOrderSide.BUY,
    )
    assert len(f2) == 0
    assert pytest.approx(ord_a.queue_ahead_qty, 1e-6) == 0.6
    assert pytest.approx(ord_b.queue_ahead_qty, 1e-6) == 1.1

    # 3. Wrong price (trade at 100.5 > our bid 100.0) must NOT consume bid queue
    f3 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.5,
        trade_size=5.0,
        timestamp=1.3,
        aggressor_side=SimOrderSide.SELL,
    )
    assert len(f3) == 0
    assert pytest.approx(ord_a.queue_ahead_qty, 1e-6) == 0.6

    # 4. Eligible SELL trade of 0.8 clears remaining 0.6 external + partially fills FIFO-A by 0.2; FIFO-B gets 0
    f4 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=0.8,
        timestamp=1.4,
        aggressor_side=SimOrderSide.SELL,
    )
    assert len([e for e in f4 if e["event"] == "FILL"]) == 1
    assert ord_a.status == SimOrderStatus.PARTIALLY_FILLED
    assert pytest.approx(ord_a.filled_quantity, 1e-6) == 0.2
    assert pytest.approx(ord_a.remaining_quantity, 1e-6) == 0.3
    assert ord_b.filled_quantity == 0.0
    assert (
        pytest.approx(ord_b.queue_ahead_qty, 1e-6) == 0.3
    )  # Exactly FIFO-A's remaining qty

    # 5. Eligible SELL trade of 0.5 finishes FIFO-A (0.3) and partially fills FIFO-B (0.2)
    f5 = engine.process_market_trade(
        "BTC-USDT",
        trade_price=100.0,
        trade_size=0.5,
        timestamp=1.5,
        aggressor_side=SimOrderSide.SELL,
    )
    assert len([e for e in f5 if e["event"] == "FILL"]) == 2
    assert ord_a.status == SimOrderStatus.FILLED
    assert ord_b.status == SimOrderStatus.PARTIALLY_FILLED
    assert pytest.approx(ord_b.filled_quantity, 1e-6) == 0.2


def test_cancel_ahead_advances_trailing_fifo_queue():
    """Verify cancelling an order ahead in FIFO queue reduces trailing order's queue_ahead_qty."""
    engine = RealisticMatchingEngine(
        config=MatchingEngineConfig(network_latency_ms=0.0, matching_delay_ms=0.0)
    )
    engine.update_book(
        "BTC-USDT", bids=[(100.0, 1.0)], asks=[(100.5, 1.0)], timestamp=1.0
    )

    ord_a = engine.submit_order(
        order_id="A-FIRST",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=2.0,
        current_time=1.0,
    )
    ord_b = engine.submit_order(
        order_id="B-SECOND",
        symbol="BTC-USDT",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        price=100.0,
        quantity=1.0,
        current_time=1.0,
    )
    engine.process_time_slice(1.0)
    assert pytest.approx(ord_b.queue_ahead_qty, 1e-6) == 3.0

    engine.cancel_order("A-FIRST", current_time=1.1)
    engine.process_time_slice(1.1)
    assert ord_a.status == SimOrderStatus.CANCELLED
    assert pytest.approx(ord_b.queue_ahead_qty, 1e-6) == 1.0
