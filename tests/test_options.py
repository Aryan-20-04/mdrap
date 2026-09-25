import sys
import os
import math
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from options import (
    OptionType,
    ExerciseStyle,
    OptionContract,
    Greeks,
    OptionPrice,
    bsm_price,
    bsm_greeks,
    price_option,
    binomial_price,
    implied_volatility,
    put_call_parity_check,
    VolatilitySurface,
    OptionsChain,
)


def test_bsm_call_known_value():
    """BSM call: S=100, K=100, T=1yr, r=5%, sigma=20%. Known theoretical ~10.45. Verify within 0.5."""
    price = bsm_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type=OptionType.CALL
    )
    assert abs(price - 10.45) < 0.5


def test_bsm_put_known_value():
    """BSM put: same params. Known theoretical ~5.57. Verify within 0.5."""
    price = bsm_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type=OptionType.PUT
    )
    assert abs(price - 5.57) < 0.5


def test_put_call_parity():
    """Compute both call and put prices. Verify C - P ≈ S - K*exp(-rT)."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    C = bsm_price(S, K, T, r, sigma, OptionType.CALL)
    P = bsm_price(S, K, T, r, sigma, OptionType.PUT)
    assert abs((C - P) - (S - K * math.exp(-r * T))) < 1e-4


def test_greeks_delta_bounds():
    """Call delta should be in [0, 1]. Put delta in [-1, 0]."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    call_greeks = bsm_greeks(S, K, T, r, sigma, OptionType.CALL)
    put_greeks = bsm_greeks(S, K, T, r, sigma, OptionType.PUT)
    assert 0.0 <= call_greeks.delta <= 1.0
    assert -1.0 <= put_greeks.delta <= 0.0


def test_greeks_gamma_positive():
    """Gamma should always be positive for both calls and puts."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    call_greeks = bsm_greeks(S, K, T, r, sigma, OptionType.CALL)
    put_greeks = bsm_greeks(S, K, T, r, sigma, OptionType.PUT)
    assert call_greeks.gamma > 0.0
    assert put_greeks.gamma > 0.0


def test_greeks_atm_delta():
    """ATM call delta should be approximately 0.5 (between 0.45-0.65 accounting for positive drift)."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    call_greeks = bsm_greeks(S, K, T, r, sigma, OptionType.CALL)
    assert 0.45 <= call_greeks.delta <= 0.65


def test_implied_volatility_roundtrip():
    """Price an option at sigma=0.25, then solve for IV from the price. IV should match 0.25 within tolerance."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.25
    market_price = bsm_price(S, K, T, r, sigma, OptionType.CALL)
    iv = implied_volatility(market_price, S, K, T, r, OptionType.CALL)
    assert abs(iv - sigma) < 1e-4


def test_binomial_vs_bsm_convergence():
    """European option: binomial price should converge to BSM price within 1% for 200+ steps."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    bsm_val = bsm_price(S, K, T, r, sigma, OptionType.CALL)
    binom_val = binomial_price(S, K, T, r, sigma, OptionType.CALL, steps=200)
    assert abs(bsm_val - binom_val) / bsm_val < 0.01


def test_binomial_american_early_exercise():
    """Deep ITM American put should be worth >= European put (early exercise premium)."""
    S, K, T, r, sigma = 50.0, 100.0, 1.0, 0.05, 0.20
    euro_put = bsm_price(S, K, T, r, sigma, OptionType.PUT)
    amer_put = binomial_price(S, K, T, r, sigma, OptionType.PUT, steps=200)
    assert amer_put >= euro_put - 1e-4


def test_volatility_surface_construction():
    """Add multiple (strike, expiry, iv) points. Verify smile() returns sorted points. Verify term_structure() works."""
    vs = VolatilitySurface()
    vs.add_point(100.0, 30, 0.20)
    vs.add_point(90.0, 30, 0.25)
    vs.add_point(110.0, 30, 0.18)
    vs.add_point(100.0, 60, 0.22)

    smile_30 = vs.smile(30)
    assert len(smile_30) == 3
    assert smile_30[0][0] == 90.0
    assert smile_30[1][0] == 100.0
    assert smile_30[2][0] == 110.0

    ts_100 = vs.term_structure(100.0)
    assert len(ts_100) == 2
    assert ts_100[0][0] == 30
    assert ts_100[1][0] == 60


def test_options_chain_generation():
    """Create chain with AAPL at $150, add expiry with strikes [140, 145, 150, 155, 160]. Verify chain() returns 10 entries."""
    chain_obj = OptionsChain(underlying="AAPL", spot=150.0, risk_free_rate=0.05)
    chain_obj.add_expiry(expiry_days=30, strikes=[140.0, 145.0, 150.0, 155.0, 160.0])

    chain_data = chain_obj.chain()
    assert len(chain_data) == 10


def test_options_chain_find_atm():
    """Verify find_atm returns the strike closest to spot."""
    chain_obj = OptionsChain(underlying="AAPL", spot=150.0, risk_free_rate=0.05)
    chain_obj.add_expiry(expiry_days=30, strikes=[140.0, 145.0, 149.0, 155.0, 160.0])
    atm_option = chain_obj.find_atm(expiry_days=30)
    assert atm_option["strike"] == 149.0


def test_edge_case_zero_time():
    """Option at expiry (T≈0): Call value should be max(S-K, 0)."""
    S, K, T, r, sigma = 100.0, 90.0, 1e-5, 0.05, 0.20
    price = bsm_price(S, K, T, r, sigma, OptionType.CALL)
    assert abs(price - max(S - K, 0.0)) < 1e-2


def test_edge_case_deep_itm():
    """Very deep ITM call (S=200, K=100): delta should be close to 1.0."""
    S, K, T, r, sigma = 200.0, 100.0, 1.0, 0.05, 0.20
    greeks = bsm_greeks(S, K, T, r, sigma, OptionType.CALL)
    assert abs(greeks.delta - 1.0) < 1e-2


def test_edge_case_deep_otm():
    """Very deep OTM call (S=50, K=100): delta should be close to 0.0."""
    S, K, T, r, sigma = 50.0, 100.0, 1.0, 0.05, 0.20
    greeks = bsm_greeks(S, K, T, r, sigma, OptionType.CALL)
    assert abs(greeks.delta - 0.0) < 1e-2
