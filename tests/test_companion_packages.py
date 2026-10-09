"""
Tests for MDRAP Phase 8 Companion Packages Modular Extraction.
Verifies that mdrap_options, mdrap_analytics, mdrap_strategies, and mdrap_vessel
can be loaded as standalone companion modules and execute correctly.
"""

import sys
import os
import pytest

# Ensure companion packages are on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg_dir in [
    os.path.join(_REPO_ROOT, "packages", "mdrap-options"),
    os.path.join(_REPO_ROOT, "packages", "mdrap-analytics"),
    os.path.join(_REPO_ROOT, "packages", "mdrap-strategies"),
    os.path.join(_REPO_ROOT, "packages", "mdrap-contrib-vessel"),
]:
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)


def test_companion_options_standalone():
    import mdrap_options as opt

    contract = opt.OptionContract(
        underlying="AAPL",
        strike=100.0,
        expiry_days=30.0,
        option_type=opt.OptionType.CALL,
    )
    p = opt.bsm_price(S=100.0, K=100.0, T=30.0 / 365.0, r=0.05, sigma=0.20, option_type=opt.OptionType.CALL)
    assert p > 0.0
    g = opt.bsm_greeks(S=100.0, K=100.0, T=30.0 / 365.0, r=0.05, sigma=0.20, option_type=opt.OptionType.CALL)
    assert 0.4 < g.delta < 0.7
    assert g.gamma > 0.0


def test_companion_analytics_standalone():
    import mdrap_analytics as tca
    from mdrap.bbo import ConsolidatedBBO

    engine = tca.TCAEngine()
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
    record = tca.ExecutionRecord(
        trade_id="TRD-01",
        symbol="AAPL",
        side="BUY",
        price=150.02,
        shares=500.0,
        timestamp=1000.005,
        venue="NASDAQ",
        broker="Interactive Brokers",
        arrival_price=150.00,
    )
    metrics = engine.evaluate_execution(record, prevailing_bbo=bbo)
    assert metrics.is_price_improved is True
    assert round(metrics.price_improvement_cents, 4) == 0.03


def test_companion_strategies_standalone():
    import mdrap_strategies as strat

    limits = strat.RiskLimits(max_order_size=500.0)
    rm = strat.RiskManager(limits=limits, initial_capital=50_000.0)
    pos = strat.Position(symbol="AAPL")
    order = strat.Order(
        order_id="1",
        symbol="AAPL",
        side=strat.OrderSide.BUY,
        order_type=strat.OrderType.MARKET,
        quantity=200.0,
    )
    ok, err = rm.validate_order(order, 150.0, pos, 50_000.0)
    assert ok is True


def test_companion_vessel_standalone():
    import mdrap_vessel as vsl

    tracker = vsl.VesselTracker()
    assert tracker is not None
