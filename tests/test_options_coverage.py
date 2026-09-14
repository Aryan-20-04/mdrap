"""
Comprehensive unit tests for src/options.py to expand code coverage.
Tests American vs European option pricing, binomial trees, IV solver convergence,
put-call parity boundary checking, VolatilitySurface interpolation, smile/term structure,
and OptionsChain max pain calculations.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from options import (
    ExerciseStyle,
    OptionContract,
    OptionPrice,
    OptionType,
    OptionsChain,
    VolatilitySurface,
    binomial_price,
    bsm_greeks,
    bsm_price,
    implied_volatility,
    price_option,
    put_call_parity_check,
)


def test_options_binomial_american_put_vs_call():
    # American Call without dividends has same price as European Call
    call_euro = bsm_price(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.CALL)
    call_amer = binomial_price(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.CALL, steps=100)
    assert abs(call_euro - call_amer) < 0.5

    # Deep ITM American put has early exercise value >= S - K
    put_amer = binomial_price(80.0, 100.0, 1.0, 0.05, 0.20, OptionType.PUT, steps=100)
    assert put_amer >= (100.0 - 80.0)

    # Test edge case: T <= 0
    assert binomial_price(100.0, 90.0, 0.0, 0.05, 0.20, OptionType.CALL) == 10.0
    assert binomial_price(100.0, 110.0, 0.0, 0.05, 0.20, OptionType.PUT) == 10.0
    assert binomial_price(100.0, 110.0, 0.0, 0.05, 0.20, OptionType.CALL) == 0.0


def test_options_contract_pricing():
    c_euro = OptionContract("AAPL", strike=150.0, expiry_days=30.0, option_type=OptionType.CALL, exercise_style=ExerciseStyle.EUROPEAN)
    p_euro = price_option(c_euro, spot=155.0, risk_free_rate=0.05, volatility=0.25)
    assert p_euro.model == "BSM"
    assert p_euro.theoretical > 0.0
    assert p_euro.intrinsic == 5.0
    assert p_euro.time_value > 0.0
    assert 0.0 <= p_euro.greeks.delta <= 1.0

    c_amer = OptionContract("AAPL", strike=150.0, expiry_days=30.0, option_type=OptionType.PUT, exercise_style=ExerciseStyle.AMERICAN)
    p_amer = price_option(c_amer, spot=140.0, risk_free_rate=0.05, volatility=0.25)
    assert p_amer.model == "BINOMIAL"
    assert p_amer.theoretical >= 10.0
    assert p_amer.intrinsic == 10.0


def test_options_iv_solver():
    # Known price at vol=0.30
    price_target = bsm_price(100.0, 100.0, 0.5, 0.05, 0.30, OptionType.CALL)
    solved_iv = implied_volatility(price_target, 100.0, 100.0, 0.5, 0.05, OptionType.CALL)
    assert abs(solved_iv - 0.30) < 1e-4

    # Put IV solving
    put_target = bsm_price(100.0, 100.0, 0.5, 0.05, 0.25, OptionType.PUT)
    solved_put_iv = implied_volatility(put_target, 100.0, 100.0, 0.5, 0.05, OptionType.PUT)
    assert abs(solved_put_iv - 0.25) < 1e-4

    # Degenerate inputs (market price below intrinsic)
    assert implied_volatility(0.0, 100.0, 50.0, 0.5, 0.05, OptionType.CALL) == 0.0


def test_options_put_call_parity():
    S = 100.0
    K = 100.0
    T = 0.5
    r = 0.05
    sigma = 0.20
    c = bsm_price(S, K, T, r, sigma, OptionType.CALL)
    p = bsm_price(S, K, T, r, sigma, OptionType.PUT)

    res = put_call_parity_check(c, p, S, K, T, r)
    assert not res["is_violated"]
    assert abs(res["violation_pct"]) < 0.1

    # Simulated violation
    bad_res = put_call_parity_check(c + 15.0, p, S, K, T, r)
    assert bad_res["is_violated"]


def test_options_volatility_surface():
    surf = VolatilitySurface()
    surf.add_point(90.0, 30.0, 0.28)
    surf.add_point(100.0, 30.0, 0.22)
    surf.add_point(110.0, 30.0, 0.20)
    surf.add_point(90.0, 60.0, 0.26)
    surf.add_point(100.0, 60.0, 0.21)
    surf.add_point(110.0, 60.0, 0.19)

    # Exact query
    assert surf.get_iv(100.0, 30.0) == 0.22

    # Nearest neighbor query
    interp = surf.get_iv(95.0, 30.0)
    assert interp is not None
    assert interp in (0.28, 0.22)

    # Smile
    smile_30 = surf.smile(30.0)
    assert len(smile_30) == 3
    assert smile_30[0] == (90.0, 0.28)

    # Term structure
    term_100 = surf.term_structure(100.0)
    assert len(term_100) == 2

    # Skew approximation
    skew = surf.skew(30.0)
    assert isinstance(skew, float)

    # to_dict serialization
    d = surf.to_dict()
    assert "points" in d


def test_options_chain_and_max_pain():
    chain = OptionsChain("AAPL", spot=150.0, risk_free_rate=0.05)
    chain.add_expiry(expiry_days=30.0, strikes=[140.0, 145.0, 150.0, 155.0, 160.0], volatility=0.25)

    entries = chain.chain()
    assert len(entries) == 10  # 5 calls + 5 puts

    atm = chain.find_atm(30.0)
    assert atm["strike"] == 150.0

    max_pain = chain.max_pain(30.0)
    assert 140.0 <= max_pain <= 160.0


def test_options_python_fallback(monkeypatch):
    import options
    monkeypatch.setattr(options, "fastpath", None)

    # European Call & Put via Python BSM
    c_px = options.bsm_price(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.CALL)
    p_px = options.bsm_price(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.PUT)
    assert 10.0 < c_px < 11.0
    assert 5.0 < p_px < 6.0

    # Greeks via Python
    g_call = options.bsm_greeks(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.CALL)
    g_put = options.bsm_greeks(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.PUT)
    assert 0.6 < g_call.delta < 0.7
    assert -0.4 < g_put.delta < -0.3
    assert g_call.gamma > 0.0
    assert g_call.vega > 0.0
    assert g_call.theta < 0.0
    assert g_call.rho > 0.0
    assert g_put.rho < 0.0

    # Binomial American Tree via Python
    bin_call = options.binomial_price(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.CALL, steps=50)
    bin_put = options.binomial_price(100.0, 100.0, 1.0, 0.05, 0.20, OptionType.PUT, steps=50)
    assert abs(bin_call - c_px) < 0.5
    assert bin_put > 5.0

    # Implied Volatility Solver via Python Newton-Raphson
    target_px = options.bsm_price(100.0, 100.0, 0.5, 0.05, 0.25, OptionType.CALL)
    iv = options.implied_volatility(target_px, 100.0, 100.0, 0.5, 0.05, OptionType.CALL)
    assert abs(iv - 0.25) < 1e-4

    # Edge cases
    assert options.bsm_price(100.0, 100.0, 0.0, 0.05, 0.20, OptionType.CALL) == 0.0
    assert options.bsm_price(100.0, 100.0, 1.0, 0.05, 0.0, OptionType.CALL) > 0.0
    g_edge = options.bsm_greeks(100.0, 100.0, 0.0, 0.05, 0.20, OptionType.CALL)
    assert g_edge.gamma == 0.0

