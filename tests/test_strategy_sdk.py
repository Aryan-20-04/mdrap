import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from strategy_sdk import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperExecutor,
    Position,
    RiskLimits,
    RiskManager,
    SpreadCaptureMarketMaker,
    Strategy,
    StrategyRunner,
    WhaleMomentumStrategy,
)
from models import CanonicalEvent, EventType, QualityStatus


def test_risk_manager_max_order_size():
    limits = RiskLimits(max_order_size=500.0)
    rm = RiskManager(limits=limits, initial_capital=50_000.0)
    pos = Position(symbol="AAPL")

    # Valid order size
    order = Order(order_id="1", symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=200.0)
    ok, err = rm.validate_order(order, 150.0, pos, 50_000.0)
    assert ok is True
    assert err is None

    # Exceeds max order size
    big_order = Order(order_id="2", symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=1000.0)
    ok, err = rm.validate_order(big_order, 150.0, pos, 50_000.0)
    assert ok is False
    assert "exceeds max order limit" in err


def test_risk_manager_price_collar():
    limits = RiskLimits(price_collar_bps=50.0)  # 0.50%
    rm = RiskManager(limits=limits)
    pos = Position(symbol="AAPL")

    # Midpoint is 100.0, 50 bps is 0.50. Range is [99.50, 100.50]
    valid_limit = Order(order_id="1", symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=100.0, price=100.30)
    ok, err = rm.validate_order(valid_limit, 100.0, pos, 100_000.0)
    assert ok is True

    # Fat-finger order: price=110.0 (1000 bps away)
    fat_finger = Order(order_id="2", symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=100.0, price=110.0)
    ok, err = rm.validate_order(fat_finger, 100.0, pos, 100_000.0)
    assert ok is False
    assert "deviates by" in err


def test_risk_manager_kill_switch_drawdown():
    limits = RiskLimits(max_drawdown_pct=5.0)
    rm = RiskManager(limits=limits, initial_capital=100_000.0)
    pos = Position(symbol="AAPL")
    order = Order(order_id="1", symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=100.0)

    # Equity drops to 94,000 (6% drawdown)
    ok, err = rm.validate_order(order, 100.0, pos, 94_000.0)
    assert ok is False
    assert "kill-switch triggered" in err
    assert rm.kill_switch_triggered is True


def test_paper_executor_market_order_fill():
    executor = PaperExecutor(initial_cash=50_000.0)
    bbo = {"bid": 150.0, "ask": 150.10, "bid_size": 500.0, "ask_size": 500.0}

    # Buy Market 100 shares
    order = executor.submit_order("AAPL", OrderSide.BUY, OrderType.MARKET, 100.0, bbo=bbo)
    assert order.status == OrderStatus.FILLED
    assert order.filled_price == 150.10
    assert executor.cash == 50_000.0 - (100.0 * 150.10)

    pos = executor.get_position("AAPL")
    assert pos.quantity == 100.0
    assert pos.avg_cost == 150.10


def test_paper_executor_pnl_roundtrip():
    executor = PaperExecutor(initial_cash=50_000.0)
    bbo_buy = {"bid": 100.0, "ask": 100.0, "bid_size": 1000.0, "ask_size": 1000.0}
    bbo_sell = {"bid": 105.0, "ask": 105.0, "bid_size": 1000.0, "ask_size": 1000.0}

    # Buy 100 @ 100
    executor.submit_order("AAPL", OrderSide.BUY, OrderType.MARKET, 100.0, bbo=bbo_buy)
    # Sell 100 @ 105
    executor.submit_order("AAPL", OrderSide.SELL, OrderType.MARKET, 100.0, bbo=bbo_sell)

    pos = executor.get_position("AAPL")
    assert pos.quantity == 0.0
    assert pos.realized_pnl == 500.0  # (105 - 100) * 100
    assert executor.cash == 50_000.0 + 500.0


def test_whale_momentum_strategy_execution():
    strat = WhaleMomentumStrategy(symbol="AAPL", trade_size=100.0)
    runner = StrategyRunner(strat)

    # Initial quote to establish BBO
    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=150.0, ask_price=150.10,
    )
    # Institutional whale buy print: 1000 shares @ 150.50 (> $100k notional)
    t1 = CanonicalEvent(
        event_id="t1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1001.0, receive_timestamp=1001.001, processing_timestamp=1001.002,
        source="FEEDX", sequence_number=2, price=150.50, quantity=1000.0,
    )

    metrics = runner.run_events([q1, t1])
    assert metrics["strategy"] == "WhaleMomentum"
    assert metrics["total_trades"] == 1
    assert strat.executor.get_position("AAPL").quantity == 100.0
    assert strat.entry_price == 150.10


def test_spread_capture_market_maker():
    strat = SpreadCaptureMarketMaker(symbol="AAPL", min_spread_bps=3.0, quote_size=50.0)
    runner = StrategyRunner(strat)

    # Wide spread quote: Bid=100.00, Ask=100.10 (10 bps spread)
    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=100.00, ask_price=100.10,
    )

    metrics = runner.run_events([q1])
    assert metrics["strategy"] == "SpreadCaptureMM"
    # Should have placed limit orders
    orders = strat.executor.orders
    assert len(orders) == 2
    assert any(o.side == OrderSide.BUY and o.price == 100.01 for o in orders)
    assert any(o.side == OrderSide.SELL and o.price == 100.09 for o in orders)
