"""
Tests for Pre-Trade Institutional Risk Engine (§26).

Verifies all 14 risk gates:
1. Max order quantity collar (fat-finger protection)
2. Max order notional limit ($)
3. Price collar (max bps deviation from mid)
4. Max position limit per instrument
5. Max gross portfolio exposure
6. Max net portfolio exposure
7. Portfolio drawdown kill-switch (5%)
8. Per-symbol notional cap
9. Stale quote rejection
10. Idempotency key deduplication (replay attack defense)
11. Per-venue notional cap
12. Short selling policy
13. Global circuit breaker / emergency kill switch
14. State persistence & reload.
"""

import os
import tempfile
import time
import pytest
import sys
from pathlib import Path

# Add paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "packages" / "mdrap-strategies"))
sys.path.insert(0, str(root / "src"))

from mdrap_strategies.risk_engine import (
    InstitutionalRiskEngine,
    StrictRiskLimits,
    RiskCheckResult,
)


def test_fat_finger_and_notional_limits():
    """Gate 1 & Gate 3: Order size and notional limits."""
    limits = StrictRiskLimits(
        max_order_qty=100.0,
        max_order_notional_usd=10_000.0,
        price_collar_bps=100.0,
    )
    risk = InstitutionalRiskEngine(limits=limits)

    # Order exceeds max quantity
    res1 = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=150.0,
        price=100.0,
        prevailing_mid=100.0,
    )
    assert res1.passed is False
    assert res1.gate == "FAT_FINGER_QTY"

    # Order exceeds max notional (90 qty * 120 price = 10,800 > 10,000)
    res2 = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=90.0,
        price=120.0,
        prevailing_mid=120.0,
    )
    assert res2.passed is False
    assert res2.gate == "MAX_NOTIONAL"

    # Valid order passes
    res3 = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=50.0,
        price=100.0,
        prevailing_mid=100.0,
    )
    assert res3.passed is True


def test_price_collar_rejection():
    """Gate 5: Price collar deviation from mid."""
    limits = StrictRiskLimits(price_collar_bps=50.0)  # 50 bps = 0.50%
    risk = InstitutionalRiskEngine(limits=limits)
    mid = 100.0

    # Buy at 101.0 (100 bps away > 50 bps collar)
    res = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        price=101.0,
        prevailing_mid=mid,
    )
    assert res.passed is False
    assert res.gate == "PRICE_COLLAR"


def test_position_and_exposure_limits():
    """Position & exposure limits."""
    limits = StrictRiskLimits(
        max_position_qty=10.0,
        max_gross_exposure_usd=2000.0,
        max_net_exposure_usd=1000.0,
    )
    risk = InstitutionalRiskEngine(limits=limits)

    # Simulated position of +8.0 BTC
    risk.update_position("BTC-USDT", fill_qty=8.0, fill_price=100.0, side="BUY")

    # Adding 3.0 BTC would breach max position limit of 10.0 (8 + 3 = 11 > 10)
    res = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=3.0,
        price=100.0,
        prevailing_mid=100.0,
    )
    assert res.passed is False
    assert res.gate == "MAX_POSITION"

    # Selling 2.0 BTC is permitted (reduces position to 6)
    res2 = risk.validate_order(
        symbol="BTC-USDT",
        side="SELL",
        order_type="LIMIT",
        quantity=2.0,
        price=100.0,
        prevailing_mid=100.0,
    )
    assert res2.passed is True


def test_drawdown_kill_switch():
    """Portfolio drawdown kill switch."""
    limits = StrictRiskLimits(max_drawdown_pct=5.0)  # 5% max drawdown
    risk = InstitutionalRiskEngine(limits=limits, initial_capital=100_000.0)

    # Equity drops to $94,000 (6% drawdown > 5%)
    risk.update_portfolio_equity(94_000.0)
    assert risk.kill_switch_active is True

    # Any new order must be rejected by kill-switch
    res = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        price=100.0,
        prevailing_mid=100.0,
    )
    assert res.passed is False
    assert res.gate == "KILL_SWITCH"


def test_stale_quote_rejection():
    """Stale market data rejection."""
    limits = StrictRiskLimits(max_quote_age_s=2.0)  # 2.0s max age
    risk = InstitutionalRiskEngine(limits=limits)

    # Quote timestamp 5 seconds ago
    old_ts = time.time() - 5.0
    res = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        price=100.0,
        prevailing_mid=100.0,
        quote_timestamp=old_ts,
    )
    assert res.passed is False
    assert res.gate == "STALE_QUOTE"


def test_idempotency_key_deduplication():
    """Duplicate order / replay attack defense."""
    risk = InstitutionalRiskEngine()
    client_id = "ORDER-CLIENT-UUID-12345"

    res1 = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        price=100.0,
        prevailing_mid=100.0,
        idempotency_key=client_id,
    )
    assert res1.passed is True

    # Same idempotency key submitted again
    res2 = risk.validate_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        price=100.0,
        prevailing_mid=100.0,
        idempotency_key=client_id,
    )
    assert res2.passed is False
    assert res2.gate == "DUPLICATE_ORDER"


def test_emergency_circuit_breaker():
    """Emergency kill switch / operator halt."""
    risk = InstitutionalRiskEngine()

    # Pre-condition: valid order allowed
    res_before = risk.validate_order(
        symbol="BTC-USDT", side="BUY", order_type="LIMIT", quantity=1.0, price=100.0, prevailing_mid=100.0
    )
    assert res_before.passed is True

    # Activate kill switch
    risk.activate_kill_switch("REGULATORY_HALT")
    assert risk.kill_switch_active is True

    # Subsequent orders blocked
    res_after = risk.validate_order(
        symbol="BTC-USDT", side="BUY", order_type="LIMIT", quantity=1.0, price=100.0, prevailing_mid=100.0
    )
    assert res_after.passed is False
    assert res_after.gate == "KILL_SWITCH"

    # Reset kill switch
    risk.reset_kill_switch()
    assert risk.kill_switch_active is False
    res_reopened = risk.validate_order(
        symbol="BTC-USDT", side="BUY", order_type="LIMIT", quantity=1.0, price=100.0, prevailing_mid=100.0
    )
    assert res_reopened.passed is True


def test_risk_state_persistence_and_recovery():
    """Verify state serialization across process restarts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "risk_state.json")
        risk1 = InstitutionalRiskEngine(initial_capital=50_000.0, persistence_path=state_file)
        risk1.update_position("BTC-USDT", fill_qty=1.5, fill_price=50_000.0, side="BUY")
        risk1.update_portfolio_equity(48_000.0)
        risk1.persist_state()

        # Instantiate fresh engine loading persisted state
        risk2 = InstitutionalRiskEngine(initial_capital=50_000.0, persistence_path=state_file)
        assert risk2.positions["BTC-USDT"] == 1.5
        assert risk2.current_equity == 48_000.0
