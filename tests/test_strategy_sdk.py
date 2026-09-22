import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from strategy_sdk import (
    Order,
    OrderBook,
    OrderBookSnapshot,
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
    AvellanedaStoikovStrategy,
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


def test_risk_manager_zero_equity_handling():
    """Verify RiskManager handles non-positive initial capital and negative equity gracefully."""
    rm = RiskManager(initial_capital=0.0)
    pos = Position(symbol="AAPL")
    order = Order(order_id="zero_eq", symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=10.0)

    # Negative equity with 0 peak equity triggers 100% drawdown kill-switch
    ok, err = rm.validate_order(order, 100.0, pos, -100.0)
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


def test_order_book_microstructure_and_depth():
    book = OrderBook("AAPL")
    book.update_quote(bid_price=150.00, ask_price=150.05, bid_size=200.0, ask_size=100.0)

    best_bid, bid_sz = book.best_bid
    best_ask, ask_sz = book.best_ask
    assert best_bid == 150.00
    assert bid_sz == 200.0
    assert best_ask == 150.05
    assert ask_sz == 100.0
    assert book.mid_price == 150.025
    assert round(book.spread, 4) == 0.05
    assert round(book.spread_bps, 2) == round(0.05 / 150.025 * 10000.0, 2)
    # micro-price should tilt towards ask because bid_size (200) > ask_size (100)
    expected_micro = (150.00 * 100.0 + 150.05 * 200.0) / 300.0
    assert round(book.micro_price, 4) == round(expected_micro, 4)
    # imbalance = (200 - 100) / 300 = +0.3333
    assert round(book.imbalance, 4) == round(100.0 / 300.0, 4)

    # Multi-tier depth
    bids, asks = book.get_ladder(depth=5)
    assert len(bids) == 5
    assert len(asks) == 5
    assert bids[0][0] > bids[1][0]  # Descending
    assert asks[0][0] < asks[1][0]  # Ascending


def test_order_book_walk_book_execution():
    book = OrderBook("AAPL")
    # Set explicit levels
    book.update_level(OrderSide.BUY, 100.00, 100.0)
    book.update_level(OrderSide.BUY, 99.90, 200.0)
    book.update_level(OrderSide.SELL, 100.10, 100.0)
    book.update_level(OrderSide.SELL, 100.20, 200.0)

    # Buy 150 shares -> should consume 100 @ 100.10, and 50 @ 100.20
    vwap, slippage_bps, slippage_usd, eff_spread_bps, rungs = book.walk_book(OrderSide.BUY, 150.0)
    expected_vwap = (100.0 * 100.10 + 50.0 * 100.20) / 150.0
    assert round(vwap, 4) == round(expected_vwap, 4)
    assert len(rungs) == 2
    assert rungs[0]["price"] == 100.10
    assert rungs[0]["size"] == 100.0
    assert rungs[1]["price"] == 100.20
    assert rungs[1]["size"] == 50.0
    assert slippage_usd > 0
    assert slippage_bps > 0


def test_strategy_order_book_snapshot_on_execution():
    strat = WhaleMomentumStrategy(symbol="AAPL", trade_size=100.0)
    runner = StrategyRunner(strat)

    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=150.00, ask_price=150.10,
    )
    t1 = CanonicalEvent(
        event_id="t1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1001.0, receive_timestamp=1001.001, processing_timestamp=1001.002,
        source="FEEDX", sequence_number=2, price=150.50, quantity=1000.0,
    )

    runner.run_events([q1, t1])

    order = strat.executor.orders[0]
    assert order.status == OrderStatus.FILLED
    assert order.order_book_snapshot is not None
    assert order.order_book_snapshot.symbol == "AAPL"
    assert order.order_book_snapshot.best_bid == 150.00
    assert order.order_book_snapshot.best_ask == 150.10
    assert order.arrival_price == 150.10
    assert "Whale Accumulation" in order.signal_reason


def test_strategy_execution_ledger():
    strat = WhaleMomentumStrategy(symbol="AAPL", trade_size=100.0)
    runner = StrategyRunner(strat)

    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=150.00, ask_price=150.10,
    )
    t1 = CanonicalEvent(
        event_id="t1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1001.0, receive_timestamp=1001.001, processing_timestamp=1001.002,
        source="FEEDX", sequence_number=2, price=150.50, quantity=1000.0,
    )

    runner.run_events([q1, t1])
    ledger = strat.get_execution_ledger()
    assert len(ledger) == 1
    trade = ledger[0]
    assert trade["trade_id"] == "trd-0001"
    assert trade["symbol"] == "AAPL"
    assert trade["side"] == "BUY"
    assert trade["quantity"] == 100.0
    assert trade["filled_price"] >= 150.10
    assert trade["order_book_snapshot"] is not None
    assert "bids" in trade["order_book_snapshot"]
    assert "asks" in trade["order_book_snapshot"]


def test_strategy_export_executions_json_and_csv(tmp_path):
    import json
    import csv

    strat = WhaleMomentumStrategy(symbol="AAPL", trade_size=100.0)
    runner = StrategyRunner(strat)

    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=150.00, ask_price=150.10,
    )
    t1 = CanonicalEvent(
        event_id="t1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1001.0, receive_timestamp=1001.001, processing_timestamp=1001.002,
        source="FEEDX", sequence_number=2, price=150.50, quantity=1000.0,
    )
    runner.run_events([q1, t1])

    # Export to JSON
    json_path = tmp_path / "executions.json"
    exported_json = strat.export_executions(str(json_path), format="json")
    assert os.path.exists(exported_json)
    with open(exported_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["strategy"] == "WhaleMomentum"
    assert data["executions_count"] == 1
    assert len(data["executions"]) == 1
    assert "current_order_books" in data
    assert "AAPL" in data["current_order_books"]

    # Export to CSV
    csv_path = tmp_path / "executions.csv"
    exported_csv = strat.export_executions(str(csv_path), format="csv")
    assert os.path.exists(exported_csv)
    with open(exported_csv, "r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2  # Header + 1 trade
    assert rows[0][0] == "trade_id"
    assert rows[1][0] == "trd-0001"
    assert rows[1][3] == "AAPL"


def test_avellaneda_stoikov_quoting_and_inventory_skew():
    strat = AvellanedaStoikovStrategy(symbol="AAPL", gamma=0.1, kappa=1.5, quote_size=50.0, max_inventory=500.0)
    runner = StrategyRunner(strat)

    # Initial quote at 100.00 / 100.05
    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=100.00, ask_price=100.05,
    )
    runner.run_events([q1])

    # Should have placed two-sided limit orders
    orders = [o for o in strat.executor.orders if o.status == OrderStatus.PENDING]
    assert len(orders) == 2
    buy_order = next(o for o in orders if o.side == OrderSide.BUY)
    sell_order = next(o for o in orders if o.side == OrderSide.SELL)
    assert buy_order.price <= 100.00
    assert sell_order.price >= 100.05

    # Simulate long inventory position
    strat.executor.get_position("AAPL").quantity = 300.0  # long 300 shares (>= 50% max_inventory)
    q2 = CanonicalEvent(
        event_id="q2", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1001.0, receive_timestamp=1001.001, processing_timestamp=1001.002,
        source="FEEDX", sequence_number=2, bid_price=100.00, ask_price=100.05,
    )
    runner.run_events([q2])

    # With long inventory, reservation price drops and strategy asymmetrically quotes only the sell side
    assert strat.reservation_prices["AAPL"] < 100.025
    active_orders = [o for o in strat.executor.orders if o.status == OrderStatus.PENDING]
    assert all(o.side == OrderSide.SELL for o in active_orders)


def test_avellaneda_stoikov_quality_shield():
    strat = AvellanedaStoikovStrategy(symbol="AAPL", gamma=0.1, kappa=1.5, quote_size=50.0)
    runner = StrategyRunner(strat)

    # 1. Crossed quote with INVALID status should be completely rejected
    crossed = CanonicalEvent(
        event_id="bad_q", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=101.00, ask_price=100.00,
        quality_status=QualityStatus.INVALID,
    )
    runner.run_events([crossed])
    assert len(strat.executor.orders) == 0

    # 2. SUSPICIOUS quote should trigger 3x spread expansion defense
    suspicious = CanonicalEvent(
        event_id="sus_q", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1001.0, receive_timestamp=1001.001, processing_timestamp=1001.002,
        source="FEEDX", sequence_number=2, bid_price=100.00, ask_price=100.06,
        quality_status=QualityStatus.SUSPICIOUS,
    )
    runner.run_events([suspicious])
    assert strat.toxic_flow_detected["AAPL"] is True
    # Spread should be at least 3x the normal half-spread
    assert strat.optimal_spreads["AAPL"] >= 0.06 * 3.0


def test_avellaneda_stoikov_passive_fills():
    strat = AvellanedaStoikovStrategy(symbol="AAPL", quote_size=50.0)
    runner = StrategyRunner(strat)

    # Quote placed: Bid=100.00, Ask=100.05
    q1 = CanonicalEvent(
        event_id="q1", instrument_id="AAPL", event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="FEEDX", sequence_number=1, bid_price=100.00, ask_price=100.05,
    )
    # Trade print crosses our buy limit (market sell trade at 99.98 <= 100.00)
    t1 = CanonicalEvent(
        event_id="t1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.1, receive_timestamp=1000.101, processing_timestamp=1000.102,
        source="FEEDX", sequence_number=2, price=99.98, quantity=100.0,
    )
    runner.run_events([q1, t1])

    pos = strat.executor.get_position("AAPL")
    assert pos.quantity == 50.0  # Bought 50 shares passively
    assert len(strat.executor.fills) == 1
    fill = strat.executor.fills[0]
    assert fill["side"] == "BUY"
    assert fill["qty"] == 50.0


