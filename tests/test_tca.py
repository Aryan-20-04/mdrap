"""
Unit tests for MDRAP Transaction Cost Analysis (TCA) & Best Execution Engine.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bbo import ConsolidatedBBO
from tca import ExecutionRecord, TCAEngine, TCAMetrics


def test_tca_single_buy_price_improvement():
    engine = TCAEngine()
    
    # Prevailing BBO: 149.95 / 150.05, Mid = 150.00
    bbo = ConsolidatedBBO(
        instrument_id="AAPL",
        best_bid=149.95,
        best_bid_size=100.0,
        best_bid_source="NASDAQ",
        best_ask=150.05,
        best_ask_size=100.0,
        best_ask_source="ARCA",
        spread=0.10,
        mid_price=150.00,
        is_crossed=False,
        is_locked=False,
        timestamp=1000.0,
    )

    # Buy filled at 150.02 (3 cents inside the 150.05 ask -> Price Improvement!)
    record = ExecutionRecord(
        trade_id="TRD-01",
        symbol="AAPL",
        side="BUY",
        price=150.02,
        shares=500.0,
        timestamp=1000.005,
        broker="Interactive Brokers",
        venue="NASDAQ",
        arrival_price=150.00,
    )

    metrics = engine.evaluate_execution(record, prevailing_bbo=bbo)
    
    # 150.05 - 150.02 = 0.03 price improvement per share
    assert metrics.is_price_improved is True
    assert metrics.is_disimproved is False
    assert round(metrics.price_improvement_cents, 4) == 0.03
    assert metrics.price_improvement_usd == 15.00  # $0.03 * 500 shares
    assert metrics.quality_score >= 85.0
    assert len(metrics.merkle_leaf_hash) == 64


def test_tca_single_sell_disimprovement_slippage():
    engine = TCAEngine()
    
    # Prevailing BBO: 149.95 / 150.05, Mid = 150.00
    bbo = ConsolidatedBBO(
        instrument_id="AAPL",
        best_bid=149.95,
        best_bid_size=100.0,
        best_bid_source="NASDAQ",
        best_ask=150.05,
        best_ask_size=100.0,
        best_ask_source="ARCA",
        spread=0.10,
        mid_price=150.00,
        is_crossed=False,
        is_locked=False,
        timestamp=1000.0,
    )

    # Sell filled at 149.90 (5 cents below prevailing 149.95 bid -> Disimprovement!)
    record = ExecutionRecord(
        trade_id="TRD-02",
        symbol="AAPL",
        side="SELL",
        price=149.90,
        shares=1000.0,
        timestamp=1000.005,
        broker="PFOF Wholesaler",
        venue="INTERNAL",
        arrival_price=150.00,
    )

    metrics = engine.evaluate_execution(record, prevailing_bbo=bbo)
    
    assert metrics.is_price_improved is False
    assert metrics.is_disimproved is True
    # Slippage = Arrival (150.00) - Exec (149.90) = +0.10 cents
    assert round(metrics.slippage_cents, 4) == 0.10
    assert metrics.slippage_bps > 0.0
    assert metrics.quality_score < 70.0


def test_tca_batch_evaluation_and_broker_scorecard():
    engine = TCAEngine()
    records = TCAEngine.generate_demo_executions(symbol="AAPL", count=50)
    
    report = engine.evaluate_batch(records)
    
    assert report["total_trades"] == 50
    assert report["total_shares"] > 0
    assert report["total_notional"] > 0
    assert "mean_slippage_bps" in report
    assert "overall_quality_score" in report
    assert len(report["merkle_root"]) == 64
    assert len(report["broker_scorecards"]) >= 2

    # Check top broker scorecard
    best_broker = report["broker_scorecards"][0]
    worst_broker = report["broker_scorecards"][-1]
    assert best_broker["score"] >= worst_broker["score"]
    assert "rating" in best_broker
