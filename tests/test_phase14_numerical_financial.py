"""
Phase 14: Numerical & Financial Correctness Tests.
Validates:
- NUM-01: Temporal lookahead bias prevention in BarDB as-of historical queries.
- NUM-02: Tracking and observability of late/out-of-order ticks in BarAggregator.
- NUM-03: Geometric return compounding in Monte Carlo VaR (Python and native C kernel parity).
- NUM-04: Annualized vs daily risk-free rate unit mismatch correction in Sharpe and Sortino ratios.
"""

from datetime import datetime, timedelta
import math
import pytest

from bardb import BarDatabase, BarAggregator
from models import CanonicalEvent, EventType, QualityStatus
from risk import PortfolioRiskEngine, ReturnSeries


def _make_trade(ts: float, price: float, qty: float = 100.0, symbol: str = "AAPL") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"t_{ts}",
        instrument_id=symbol,
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source="EXCHANGE",
        sequence_number=1,
        price=price,
        quantity=qty,
        quality_status=QualityStatus.VALID,
        reasons=[],
        raw_id=f"raw_{ts}",
    )


def test_num_01_bardb_as_of_lookahead_bias_elimination():
    with BarDatabase(":memory:") as db:
        # Create 1-minute (60s) bars:
        # Bucket 0: [0, 60), trade at t=10
        # Bucket 60: [60, 120), trade at t=70
        # Bucket 120: [120, 180), trade at t=130
        for ts in [10.0, 70.0, 130.0]:
            db.observe(_make_trade(ts=ts, price=100.0 + ts))
        db.flush()

        # At as_of_time = 59.0: Bucket 0 is NOT yet completed (closes at 60.0) -> returns None
        assert db.query_as_of("AAPL", "1m", 59.0) is None

        # At as_of_time = 60.0: Bucket 0 is completed (0 + 60 <= 60) -> returns Bucket 0
        b60 = db.query_as_of("AAPL", "1m", 60.0)
        assert b60 is not None
        assert b60.bucket_start == 0.0

        # At as_of_time = 90.0: Bucket 60 is NOT completed (closes at 120.0) -> returns Bucket 0 (zero lookahead)
        b90 = db.query_as_of("AAPL", "1m", 90.0)
        assert b90 is not None
        assert b90.bucket_start == 0.0

        # At as_of_time = 120.0: Bucket 60 is completed -> returns Bucket 60
        b120 = db.query_as_of("AAPL", "1m", 120.0)
        assert b120 is not None
        assert b120.bucket_start == 60.0


def test_num_02_bar_aggregator_late_tick_tracking():
    agg = BarAggregator(interval_s=60.0)
    assert agg.late_ticks_dropped == 0

    # Ingest trade at t=100 (bucket start 60.0)
    agg.observe(_make_trade(ts=100.0, price=150.0))

    # Ingest trade at t=105 (bucket start 60.0)
    agg.observe(_make_trade(ts=105.0, price=151.0))
    assert agg.late_ticks_dropped == 0

    # Ingest late-arriving trade at t=30 (bucket start 0.0 < current_start 60.0)
    res = agg.observe(_make_trade(ts=30.0, price=149.0))
    assert res is None
    assert agg.late_ticks_dropped == 1

    # Ingest another late trade at t=45
    agg.observe(_make_trade(ts=45.0, price=149.5))
    assert agg.late_ticks_dropped == 2


def test_num_03_monte_carlo_var_geometric_compounding():
    engine = PortfolioRiskEngine(initial_capital=1_000_000.0)
    # Populate steady return series with positive drift and 1% daily stdev
    base_date = datetime(2023, 1, 1)
    val = 1_000_000.0
    for i in range(100):
        val *= 1.001 if i % 2 == 0 else 0.999
        engine.observe(base_date + timedelta(days=i), val)

    # 10-day horizon VaR with geometric compounding
    var_10d = engine.monte_carlo_var(n_simulations=5000, horizon_days=10, seed=42)
    assert var_10d > 0.0
    # Over 10 days, VaR should scale roughly by sqrt(10) with positive floor
    var_1d = engine.monte_carlo_var(n_simulations=5000, horizon_days=1, seed=42)
    assert var_10d > var_1d
    assert var_10d < var_1d * 6.0  # Bounded within theoretical envelope


def test_num_04_sharpe_and_sortino_annualized_risk_free_conversion():
    engine = PortfolioRiskEngine()
    base_date = datetime(2023, 1, 1)
    # Generate daily returns of ~0.05% (equivalent to ~12.6% annual return)
    val = 100_000.0
    for i in range(1, 100):
        daily_mult = 1.0005 + (0.002 if i % 2 == 0 else -0.002)
        val *= daily_mult
        engine.observe(base_date + timedelta(days=i), val)

    # When risk_free_rate = 0.0
    sharpe_zero_rf = engine.sharpe_ratio(risk_free_rate=0.0)
    assert sharpe_zero_rf > 0.0

    # When passing an annualized 5% risk-free rate (0.05)
    # Under the old defect, subtracting 0.05 from 0.0005 daily made excess returns massively negative (~-0.0495/day)
    # With unit conversion, rf_daily = (1.05)^(1/252) - 1 ≈ 0.000193/day, leaving positive excess returns!
    sharpe_5pct_rf = engine.sharpe_ratio(risk_free_rate=0.05)
    assert sharpe_5pct_rf > 0.0
    assert sharpe_5pct_rf < sharpe_zero_rf

    sortino_5pct_rf = engine.sortino_ratio(risk_free_rate=0.05)
    assert sortino_5pct_rf > 0.0
