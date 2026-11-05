"""
MDRAP v5.3 Remediation Verification Suite.

Tests for:
1. FastQualityEngine force_pure fallback parity & isolation
2. AS-MM vs Linear Inventory Skew MM quote divergence
3. DenominationAwareValuator cross-venue triangular basis & fee evaluation
4. ExecutionTearSheet institutional risk metrics (PSR, DSR, Calmar, CI)
5. RealisticMatchingEngine duplicate guard, kill switch, and stale data rejection
"""

import math
import os
import statistics
import sys
import pytest

# Ensure packages are importable
sys.path.insert(0, os.path.abspath("packages/mdrap-strategies"))

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.fastpath import FastQualityEngine
from mdrap.quality import QualityEngine, QualityConfig
import mdrap_strategies as ms
from mdrap_strategies.matching_engine import (
    RealisticMatchingEngine,
    SimOrderSide,
    SimOrderType,
    SimOrderStatus,
)


def test_fastpath_force_pure_python_behavior():
    """Verify FastQualityEngine(force_pure=True) completely disables native C and uses QualityEngine."""
    fqe_pure = FastQualityEngine(force_pure=True)
    assert not fqe_pure.is_native, "force_pure=True must set is_native=False"
    assert fqe_pure._engine_ptr_c is None, "force_pure=True must not instantiate C engine"
    assert fqe_pure._fallback_engine is not None, "force_pure=True must instantiate fallback QualityEngine"
    assert isinstance(fqe_pure._fallback_engine, QualityEngine)

    # Parity comparison with native engine
    fqe_native = FastQualityEngine()
    test_events = [
        # 1. Valid event
        CanonicalEvent(
            event_id="EVT-1",
            source="FEEDX",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=100.0,
            receive_timestamp=100.001,
            sequence_number=1,
            price=150.0,
            quantity=100.0,
        ),
        # 2. Duplicate event
        CanonicalEvent(
            event_id="EVT-2",
            source="FEEDX",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=100.0,
            receive_timestamp=100.002,
            sequence_number=1,
            price=150.0,
            quantity=100.0,
        ),
        # 3. Crossed quote
        CanonicalEvent(
            event_id="EVT-3",
            source="FEEDX",
            instrument_id="AAPL",
            event_type=EventType.BBO,
            exchange_timestamp=101.0,
            receive_timestamp=101.001,
            sequence_number=2,
            bid_price=155.0,
            ask_price=150.0,
        ),
    ]

    for ev in test_events:
        res_pure = fqe_pure.evaluate(ev)
        res_native = fqe_native.evaluate(ev)
        assert res_pure.quality_status == res_native.quality_status, (
            f"Status mismatch for ev seq {ev.sequence_number}: {res_pure.quality_status} vs {res_native.quality_status}"
        )


def test_as_mm_vs_linear_inventory_skew_decoupling():
    """Verify Avellaneda-Stoikov and Linear Inventory Skew models produce distinct reservation prices."""
    mid = 100.0
    inventory = 5.0
    vol = 0.02

    as_quotes = ms.compute_avellaneda_stoikov(
        mid_price=mid,
        inventory=inventory,
        volatility_sigma=vol,
        gamma=0.1,
        kappa=1.5,
    )

    linear_quotes = ms.compute_linear_inventory_skew(
        mid_price=mid,
        inventory=inventory,
        half_spread=0.05,
        inventory_penalty=0.02,
    )

    # In AS-MM: reservation = s - (q * gamma * sigma^2 * T) = 100 - (5 * 0.1 * 0.0004 * 1) = 99.9998
    # In Linear Skew: reservation = s - (q * lambda) = 100 - (5 * 0.02) = 99.90
    assert as_quotes.reservation_price != linear_quotes.reservation_price
    assert abs(as_quotes.reservation_price - 99.9998) < 1e-4
    assert abs(linear_quotes.reservation_price - 99.90) < 1e-4
    assert as_quotes.bid_quote != linear_quotes.bid_quote


def test_denomination_aware_arbitrage_valuation():
    """Verify DenominationAwareValuator rejects illusory spreads from USDT basis & taker fees."""
    valuator = ms.DenominationAwareValuator()

    # Case 1: Forensic Case (§3.4)
    # Coinbase BTC-USD = $64,200 (fee 4 bps), Binance BTC-USDT = $64,276.09 (fee 7 bps)
    # Raw spread = +$76.09. But USDT = 0.9990 USD.
    res_illusory = valuator.analyze_cross_venue_spread(
        buy_venue="Coinbase",
        buy_symbol="BTC-USD",
        buy_price=64200.0,
        buy_taker_fee_bps=4.0,
        sell_venue="Binance",
        sell_symbol="BTC-USDT",
        sell_price=64276.09,
        sell_taker_fee_bps=7.0,
        slippage_buffer_bps=1.0,
    )
    assert not res_illusory.is_profitable
    assert res_illusory.net_spread_usd < 0.0
    assert res_illusory.rejection_reason == "FEE_DRAG_UNPROFITABLE"

    # Case 2: Genuine wide profitable arbitrage spread (> 30 bps net)
    res_profitable = valuator.analyze_cross_venue_spread(
        buy_venue="Coinbase",
        buy_symbol="BTC-USD",
        buy_price=64000.0,
        buy_taker_fee_bps=4.0,
        sell_venue="Kraken",
        sell_symbol="BTC-USD",
        sell_price=64300.0,
        sell_taker_fee_bps=4.0,
        slippage_buffer_bps=1.0,
    )
    assert res_profitable.is_profitable
    assert res_profitable.net_spread_usd > 200.0
    assert res_profitable.rejection_reason == "PROFITABLE"


def test_tear_sheet_institutional_risk_metrics():
    """Verify Probabilistic Sharpe, Deflated Sharpe, Calmar, and CI calculations."""
    returns = [0.001, 0.002, -0.0005, 0.0015, 0.0008, 0.0012, -0.0003, 0.0025, 0.0011, 0.0009]
    mean_ret = statistics.mean(returns)
    std_ret = statistics.pstdev(returns)
    ann_sr = (mean_ret / std_ret) * math.sqrt(252.0)

    psr = ms.compute_probabilistic_sharpe_ratio(ann_sr, 0.0, len(returns), 0.0, 3.0)
    assert 0.0 <= psr <= 1.0
    assert psr > 0.90, "Consistently positive returns must have high PSR"

    dsr = ms.compute_deflated_sharpe_ratio(ann_sr, 10, len(returns), 0.0, 3.0)
    assert 0.0 <= dsr <= psr, "DSR must penalize for 10 trials relative to PSR"

    calmar = ms.compute_calmar_ratio(annualized_return_pct=25.0, max_drawdown_pct=5.0)
    assert abs(calmar - 5.0) < 1e-4

    ci_low, ci_high = ms.compute_sharpe_confidence_interval(ann_sr, len(returns))
    assert ci_low < ann_sr < ci_high


def test_matching_engine_duplicate_order_protection():
    """Verify RealisticMatchingEngine rejects duplicate order submissions."""
    me = RealisticMatchingEngine()
    o1 = me.submit_order(
        order_id="ORD-100",
        symbol="BTC-USD",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        quantity=1.0,
        price=50000.0,
    )
    assert o1.status in (SimOrderStatus.PENDING, SimOrderStatus.ACCEPTED)

    o2 = me.submit_order(
        order_id="ORD-100",
        symbol="BTC-USD",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        quantity=1.0,
        price=50000.0,
    )
    assert o2.status == SimOrderStatus.REJECTED
    assert o2.reject_reason == "DUPLICATE_ORDER_ID"


def test_matching_engine_kill_switch_and_cancel_all():
    """Verify kill switch rejects new orders and cancel_all revokes resting orders."""
    me = RealisticMatchingEngine()
    me.submit_order(
        order_id="ORD-A",
        symbol="BTC-USD",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        quantity=1.0,
        price=50000.0,
    )
    me.submit_order(
        order_id="ORD-B",
        symbol="BTC-USD",
        side=SimOrderSide.SELL,
        order_type=SimOrderType.LIMIT,
        quantity=1.0,
        price=51000.0,
    )

    # Engage kill switch
    me.activate_kill_switch()
    assert me.kill_switch_active

    o_blocked = me.submit_order(
        order_id="ORD-C",
        symbol="BTC-USD",
        side=SimOrderSide.BUY,
        order_type=SimOrderType.LIMIT,
        quantity=1.0,
        price=50000.0,
    )
    assert o_blocked.status == SimOrderStatus.REJECTED
    assert o_blocked.reject_reason == "KILL_SWITCH_ACTIVE"

    # Emergency cancel all
    cancelled_count = me.cancel_all(symbol="BTC-USD")
    assert cancelled_count == 2
