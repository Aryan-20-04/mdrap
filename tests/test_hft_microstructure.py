"""
Tests for Market Microstructure & Quantitative Alpha (§27).

Verifies:
- Avellaneda-Stoikov (2008) reservation price & optimal spread formulas.
- Volume-weighted Micro-Price (Stoikov 2018).
- Multi-level Order Flow Imbalance (Cont, Kukanov & Stoikov 2014).
- Lee-Ready (1991) trade aggressor classification.
- Statistical significance evaluation of alpha signals (t-statistic, p-value).
"""

import math
import pytest
import sys
from pathlib import Path

# Add paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "packages" / "mdrap-strategies"))
sys.path.insert(0, str(root / "src"))

from mdrap_strategies.microstructure import (
    compute_avellaneda_stoikov,
    compute_micro_price,
    compute_order_flow_imbalance,
    classify_trade_lee_ready,
    evaluate_signal_predictive_power,
)


def test_avellaneda_stoikov_inventory_skew():
    """
    Verify Avellaneda-Stoikov reservation price:
    r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
    When q > 0 (long inventory), r < s (reservation price drops below mid to encourage selling).
    When q < 0 (short inventory), r > s (reservation price rises above mid to encourage buying).
    """
    mid = 100.0
    gamma = 0.1
    sigma = 0.02
    time_horizon = 1.0

    # Neutral inventory (q = 0)
    res_neutral = compute_avellaneda_stoikov(
        mid_price=mid,
        inventory=0.0,
        volatility_sigma=sigma,
        gamma=gamma,
        kappa=1.5,
        time_remaining=time_horizon,
    )
    assert res_neutral.reservation_price == mid
    assert res_neutral.optimal_half_spread > 0
    assert pytest.approx(res_neutral.bid_quote, 0.01) == mid - res_neutral.optimal_half_spread
    assert pytest.approx(res_neutral.ask_quote, 0.01) == mid + res_neutral.optimal_half_spread

    # Long inventory (q = 10.0) -> reservation price must skew downward
    res_long = compute_avellaneda_stoikov(
        mid_price=mid,
        inventory=10.0,
        volatility_sigma=sigma,
        gamma=gamma,
        kappa=1.5,
        time_remaining=time_horizon,
    )
    assert res_long.reservation_price < mid
    # Bid should be posted lower to discourage further buying
    assert res_long.bid_quote <= res_neutral.bid_quote
    # Ask should be lower to incentivize aggressive selling
    assert res_long.ask_quote <= res_neutral.ask_quote

    # Short inventory (q = -10.0) -> reservation price must skew upward
    res_short = compute_avellaneda_stoikov(
        mid_price=mid,
        inventory=-10.0,
        volatility_sigma=sigma,
        gamma=gamma,
        kappa=1.5,
        time_remaining=time_horizon,
    )
    assert res_short.reservation_price > mid
    assert res_short.bid_quote >= res_neutral.bid_quote
    assert res_short.ask_quote >= res_neutral.ask_quote


def test_avellaneda_stoikov_volatility_sensitivity():
    """As volatility sigma increases, optimal half-spread delta must widen."""
    low_vol = compute_avellaneda_stoikov(
        mid_price=100.0, inventory=0.0, volatility_sigma=0.01, gamma=0.1, kappa=1.5
    )
    high_vol = compute_avellaneda_stoikov(
        mid_price=100.0, inventory=0.0, volatility_sigma=0.05, gamma=0.1, kappa=1.5
    )
    assert high_vol.optimal_half_spread > low_vol.optimal_half_spread


def test_micro_price_volume_weighting():
    """
    Micro-price P_micro = (bid * ask_qty + ask * bid_qty) / (bid_qty + ask_qty)
    When bid_size >> ask_size, buying pressure pushes micro-price towards ask.
    """
    bid = 100.0
    ask = 100.2
    # Symmetric book: micro-price equals simple mid (100.10)
    mp_sym = compute_micro_price(best_bid=bid, best_ask=ask, bid_size=10.0, ask_size=10.0)
    assert pytest.approx(mp_sym, 0.001) == 100.10

    # Heavy bid queue (bid_size=90, ask_size=10)
    # micro-price = (90 * 100.2 + 10 * 100.0) / 100 = 100.18 (skewed towards ask)
    mp_bid_heavy = compute_micro_price(best_bid=bid, best_ask=ask, bid_size=90.0, ask_size=10.0)
    assert pytest.approx(mp_bid_heavy, 0.001) == 100.18
    assert mp_bid_heavy > 100.10

    # Heavy ask queue (bid_size=10, ask_size=90)
    mp_ask_heavy = compute_micro_price(best_bid=bid, best_ask=ask, bid_size=10.0, ask_size=90.0)
    assert pytest.approx(mp_ask_heavy, 0.001) == 100.02
    assert mp_ask_heavy < 100.10


def test_order_flow_imbalance_cont_kukanov():
    """
    Cont, Kukanov & Stoikov (2014) OFI:
    - If bid rises: +bid_qty
    - If bid unchanged: +(bid_qty - prev_bid_qty)
    - If bid drops: -prev_bid_qty
    Combined symmetrically with ask side.
    """
    # Step 1: Baseline book
    # Prev: bid 100.0 (qty 10), ask 100.2 (qty 10)
    # Step 2: Bid increases from 100.0 to 100.1 (aggressive buyers stepping in)
    ofi_bid_up = compute_order_flow_imbalance(
        prev_bid_px=100.0,
        prev_bid_sz=10.0,
        curr_bid_px=100.1,
        curr_bid_sz=15.0,
        prev_ask_px=100.2,
        prev_ask_sz=10.0,
        curr_ask_px=100.2,
        curr_ask_sz=10.0,
    )
    assert ofi_bid_up > 0  # Net positive buy pressure (+15.0)

    # Step 3: Ask drops from 100.2 to 100.1 (aggressive sellers stepping in)
    ofi_ask_down = compute_order_flow_imbalance(
        prev_bid_px=100.0,
        prev_bid_sz=10.0,
        curr_bid_px=100.0,
        curr_bid_sz=10.0,
        prev_ask_px=100.2,
        prev_ask_sz=10.0,
        curr_ask_px=100.1,
        curr_ask_sz=20.0,
    )
    assert ofi_ask_down < 0  # Net negative sell pressure


def test_lee_ready_classification():
    """Lee-Ready (1991) trade side classification."""
    bid = 100.0
    ask = 100.2

    # Trade above mid -> Buyer initiated
    assert classify_trade_lee_ready(trade_price=100.15, prev_trade_price=100.1, bid_price=bid, ask_price=ask) == "BUY"

    # Trade below mid -> Seller initiated
    assert classify_trade_lee_ready(trade_price=100.05, prev_trade_price=100.1, bid_price=bid, ask_price=ask) == "SELL"

    # Trade exactly at mid -> fallback to tick rule
    # Up-tick (price > prev_price) -> Buyer initiated
    assert classify_trade_lee_ready(trade_price=100.1, prev_trade_price=100.05, bid_price=bid, ask_price=ask) == "BUY"
    # Down-tick (price < prev_price) -> Seller initiated
    assert classify_trade_lee_ready(trade_price=100.1, prev_trade_price=100.15, bid_price=bid, ask_price=ask) == "SELL"


def test_signal_predictive_power_statistics():
    """Verify t-statistic and p-value calculation for alpha signals."""
    returns = [0.005, 0.008, 0.006, 0.007, 0.009, 0.005, 0.006, 0.008, 0.007, 0.006] * 5
    res = evaluate_signal_predictive_power(returns)
    assert res["statistically_significant"] is True
    assert res["t_statistic"] > 2.0
    assert res["p_value"] < 0.05
