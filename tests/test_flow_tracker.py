"""
Unit tests for MDRAP Institutional Order Flow Tracker & Lee-Ready Classifier.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_tracker import (
    AggressorSide,
    FlowCategory,
    LeeReadyClassifier,
    OrderFlowTracker,
    ParticipantStats,
)


def test_lee_ready_quote_rule_buyer_initiated():
    classifier = LeeReadyClassifier()
    # NBBO: 149.90 / 150.10, Mid = 150.00
    # Trade at 150.08 is clearly above mid -> BUY
    side = classifier.classify(trade_price=150.08, bid_price=149.90, ask_price=150.10)
    assert side == AggressorSide.BUY


def test_lee_ready_quote_rule_seller_initiated():
    classifier = LeeReadyClassifier()
    # NBBO: 149.90 / 150.10, Mid = 150.00
    # Trade at 149.92 is clearly below mid -> SELL
    side = classifier.classify(trade_price=149.92, bid_price=149.90, ask_price=150.10)
    assert side == AggressorSide.SELL


def test_lee_ready_tick_rule_fallback_on_midpoint():
    classifier = LeeReadyClassifier()
    # Midpoint trade at 150.00
    # Trade 1: at 150.00 -> default BUY
    s1 = classifier.classify(150.00, 149.90, 150.10)
    assert s1 in (AggressorSide.BUY, AggressorSide.MID)

    # Trade 2: at 150.02 (uptick vs 150.00) -> BUY
    s2 = classifier.classify(150.02, None, None)
    assert s2 == AggressorSide.BUY

    # Trade 3: at 149.98 (downtick vs 150.02) -> SELL
    s3 = classifier.classify(149.98, None, None)
    assert s3 == AggressorSide.SELL

    # Trade 4: at 149.98 (zero-tick) -> preserves previous SELL
    s4 = classifier.classify(149.98, None, None)
    assert s4 == AggressorSide.SELL


def test_order_flow_tracker_cvd_and_aggregation():
    tracker = OrderFlowTracker("AAPL")

    # Ingest 3 trades:
    # 1. Buy 200 shares at 150.10 ($30,020 notional -> MEDIUM)
    t1 = tracker.observe_trade(
        price=150.10,
        size=200.0,
        timestamp=1000.0,
        bid=149.90,
        ask=150.10,
        broker="GSCO",
    )
    assert t1.aggressor_side == AggressorSide.BUY
    assert t1.flow_category == FlowCategory.MEDIUM

    # 2. Sell 200 shares at 149.90
    t2 = tracker.observe_trade(
        price=149.90,
        size=200.0,
        timestamp=1001.0,
        bid=149.90,
        ask=150.10,
        broker="MSCO",
    )
    assert t2.aggressor_side == AggressorSide.SELL

    # 3. Buy 15,000 shares at 150.10 (Whale block)
    t3 = tracker.observe_trade(
        price=150.10,
        size=15000.0,
        timestamp=1002.0,
        bid=149.90,
        ask=150.10,
        broker="GSCO",
    )
    assert t3.aggressor_side == AggressorSide.BUY
    assert t3.flow_category == FlowCategory.WHALE

    summary = tracker.summary()
    assert summary["symbol"] == "AAPL"
    assert summary["total_trades"] == 3
    assert summary["buy_volume"] == 15200.0
    assert summary["sell_volume"] == 200.0
    # CVD = 15,200 - 200 = 15,000
    assert summary["cvd"] == 15000.0
    assert summary["whale_trades"] == 1
    assert "ACCUMULATION" in summary["institutional_bias"]

    # Verify Goldman Sachs is net buyer
    gs = tracker.participants["GSCO"]
    assert gs.buy_volume == 15200.0
    assert gs.sell_volume == 0.0
    assert gs.stance == "ACCUMULATING"
