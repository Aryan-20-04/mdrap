"""
Tests for Portfolio Risk Management Engine
"""

import sys
import os
import math
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from risk import (
    ReturnSeries,
    PortfolioRiskEngine,
    CorrelationMatrix,
    PositionLimits,
    DrawdownCircuitBreaker,
)


def test_return_series_basic():
    rs = ReturnSeries()
    timestamps = [datetime(2023, 1, i) for i in range(1, 6)]
    values = [100.0, 102.0, 101.0, 105.0, 103.0]
    for ts, val in zip(timestamps, values):
        rs.observe(ts, val)

    ret = rs.returns()
    assert len(ret) == 4
    assert math.isclose(ret[0], 0.02)
    assert math.isclose(ret[1], -0.00980392, rel_tol=1e-4)
    assert math.isclose(ret[2], 0.03960396, rel_tol=1e-4)
    assert math.isclose(ret[3], -0.0190476, rel_tol=1e-4)

    log_ret = rs.log_returns()
    assert len(log_ret) == 4
    assert math.isclose(log_ret[0], math.log(1.02))


def test_historical_var():
    engine = PortfolioRiskEngine(confidence_level=0.95)
    for i in range(100):
        # Mostly up, occasionally down
        val = 100_000 * (1 + 0.001 * i + (-0.05 if i % 10 == 0 else 0))
        engine.observe(datetime(2023, 1, 1) + timedelta(days=i), val)

    var = engine.historical_var(confidence=0.95)
    assert var > 0


def test_parametric_var():
    engine = PortfolioRiskEngine(confidence_level=0.95)
    for i in range(100):
        val = 100_000 * (1 + 0.001 * i + (0.01 if i % 2 == 0 else -0.01))
        engine.observe(datetime(2023, 1, 1) + timedelta(days=i), val)

    var = engine.parametric_var(confidence=0.95)
    assert var > 0


def test_monte_carlo_var_deterministic():
    engine = PortfolioRiskEngine()
    for i in range(50):
        val = 100_000 * (1 + 0.01 * (i % 2))
        engine.observe(datetime(2023, 1, 1) + timedelta(days=i), val)

    var1 = engine.monte_carlo_var(n_simulations=1000, horizon_days=1, seed=42)
    var2 = engine.monte_carlo_var(n_simulations=1000, horizon_days=1, seed=42)
    assert var1 == var2


def test_expected_shortfall_exceeds_var():
    engine = PortfolioRiskEngine(confidence_level=0.95)
    for i in range(100):
        val = 100_000 * (1 + 0.001 * i + (-0.05 if i % 10 == 0 else 0))
        engine.observe(datetime(2023, 1, 1) + timedelta(days=i), val)

    var = engine.historical_var(confidence=0.95)
    cvar = engine.expected_shortfall(confidence=0.95)
    assert cvar >= var


def test_max_drawdown_computation():
    engine = PortfolioRiskEngine()
    values = [100.0, 120.0, 96.0, 110.0]
    for i, val in enumerate(values):
        engine.observe(datetime(2023, 1, i + 1), val)

    max_dd, peak_ts, trough_ts = engine.max_drawdown()
    assert math.isclose(max_dd, 20.0)  # (120 - 96) / 120 * 100.0
    assert peak_ts == datetime(2023, 1, 2)
    assert trough_ts == datetime(2023, 1, 3)


def test_sharpe_ratio():
    engine = PortfolioRiskEngine()
    for i in range(1, 100):
        # Needs variance for denominator
        val = 100_000 * (1.001**i) * (1 + 0.001 * (i % 2))
        engine.observe(datetime(2023, 1, 1) + timedelta(days=i), val)

    sharpe = engine.sharpe_ratio()
    assert sharpe > 0


def test_correlation_matrix():
    cm = CorrelationMatrix()
    for i in range(20):
        ret_a = i * 0.01
        ret_b = i * 0.01
        ret_c = (i % 2) * 0.01
        cm.observe("A", ret_a)
        cm.observe("B", ret_b)
        cm.observe("C", ret_c)

    cm.compute()
    most_corr = cm.most_correlated(1)
    assert most_corr is not None
    cr = cm.concentration_risk()
    assert cr is not None


def test_drawdown_circuit_breaker_levels():
    cb = DrawdownCircuitBreaker(warning_pct=3.0, critical_pct=5.0, kill_pct=10.0)
    assert cb.check(1.0) == "OK"
    assert cb.check(4.0) == "WARNING"
    assert cb.check(6.0) == "CRITICAL"
    assert cb.check(11.0) == "KILL"

    assert getattr(cb, "triggered", False) or getattr(cb, "level", "") == "KILL"


def test_position_limits_defaults():
    limits = PositionLimits(
        max_per_symbol=1000.0,
        max_gross_exposure=10000.0,
        max_net_exposure=5000.0,
        max_sector_pct=0.2,
        max_single_name_pct=0.1,
    )
    assert limits.max_per_symbol == 1000.0
    assert limits.max_gross_exposure == 10000.0
    assert limits.max_net_exposure == 5000.0
    assert limits.max_sector_pct == 0.2
    assert limits.max_single_name_pct == 0.1


def test_risk_summary():
    engine = PortfolioRiskEngine()
    for i in range(100):
        engine.observe(
            datetime(2023, 1, 1) + timedelta(days=i),
            100_000 * (1.001**i) * (1 + 0.001 * (i % 2)),
        )

    summary = engine.summary()
    assert isinstance(summary, dict)


def test_circuit_breaker_triggers_on_max_drawdown():
    engine = PortfolioRiskEngine()
    # 15% drawdown: 100 -> 85
    engine.observe(datetime(2023, 1, 1), 100.0)
    engine.observe(datetime(2023, 1, 2), 85.0)
    max_dd, _, _ = engine.max_drawdown()
    assert math.isclose(max_dd, 15.0)

    breaker = DrawdownCircuitBreaker(warning_pct=3.0, critical_pct=5.0, kill_pct=10.0)
    level = breaker.check(max_dd)
    assert level == "KILL"
    assert breaker.triggered
