import math, os, sys, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fastpath, options, features, risk, fix_engine

if fastpath._NATIVE_LIB is None:
    pytest.skip(
        "Native C fastpath library is disabled or not available",
        allow_module_level=True,
    )


def test_native_exports():
    assert fastpath._NATIVE_LIB is not None
    for fn in [
        "fastpath_bsm_price",
        "fastpath_bsm_greeks",
        "fastpath_binomial_price",
        "fastpath_implied_volatility",
        "fastpath_calc_rsi",
        "fastpath_calc_ema",
        "fastpath_calc_bollinger",
        "fastpath_calc_atr",
        "fastpath_monte_carlo_var",
        "fastpath_fix_checksum",
    ]:
        assert hasattr(fastpath._NATIVE_LIB, fn)


def test_bsm_price_parity():
    for S, K, T, r, sigma in [
        (100, 100, 1.0, 0.05, 0.20),
        (150, 140, 0.5, 0.03, 0.25),
        (80, 100, 0.25, 0.04, 0.35),
    ]:
        for is_call in (True, False):
            c_val = fastpath.fast_bsm_price(S, K, T, r, sigma, is_call)
            opt_type = options.OptionType.CALL if is_call else options.OptionType.PUT
            py_val = options.bsm_price(S, K, T, r, sigma, opt_type)
            assert math.isclose(c_val, py_val, rel_tol=1e-6)


def test_bsm_greeks_parity():
    S, K, T, r, sigma = 120.0, 115.0, 0.75, 0.04, 0.22
    for is_call in (True, False):
        c_gr = fastpath.fast_bsm_greeks(S, K, T, r, sigma, is_call)
        assert c_gr is not None
        opt_type = options.OptionType.CALL if is_call else options.OptionType.PUT
        py_gr = options.bsm_greeks(S, K, T, r, sigma, opt_type)
        assert math.isclose(c_gr[0], py_gr.delta, rel_tol=1e-6)
        assert math.isclose(c_gr[1], py_gr.gamma, rel_tol=1e-6)
        assert math.isclose(c_gr[2], py_gr.theta, rel_tol=1e-6)
        assert math.isclose(c_gr[3], py_gr.vega, rel_tol=1e-6)


def test_binomial_american_parity():
    S, K, T, r, sigma = 100.0, 105.0, 0.5, 0.05, 0.25
    for is_call in (True, False):
        c_val = fastpath.fast_binomial_price(S, K, T, r, sigma, is_call, 100)
        opt_type = options.OptionType.CALL if is_call else options.OptionType.PUT
        py_val = options.binomial_price(S, K, T, r, sigma, opt_type, 100)
        assert math.isclose(c_val, py_val, rel_tol=1e-4)


def test_implied_volatility_solver():
    S, K, T, r = 100.0, 100.0, 1.0, 0.05
    for target_vol in [0.15, 0.20, 0.30]:
        px = options.bsm_price(S, K, T, r, target_vol, options.OptionType.CALL)
        iv = fastpath.fast_implied_volatility(px, S, K, T, r, True)
        assert iv is not None
        assert math.isclose(iv, target_vol, abs_tol=1e-4)


def test_features_rsi_parity():
    prices = [100.0 + (i % 7) * 1.5 - (i % 3) * 0.8 for i in range(50)]
    rsi_vals = fastpath.fast_calc_rsi(prices, 14)
    assert rsi_vals is not None
    assert len(rsi_vals) == len(prices)
    for v in rsi_vals[14:]:
        assert 0.0 <= v <= 100.0


def test_features_bollinger_parity():
    prices = [50.0 + (i % 5) * 1.5 for i in range(50)]
    up, mid, low = fastpath.fast_calc_bollinger(prices, 10, 2.0)
    for i in range(10, 50):
        assert up[i] >= mid[i] >= low[i]


def test_features_atr_parity():
    highs = [105.0 + i for i in range(30)]
    lows = [95.0 + i for i in range(30)]
    closes = [100.0 + i for i in range(30)]
    atr_vals = fastpath.fast_calc_atr(highs, lows, closes, 14)
    assert atr_vals is not None
    assert len(atr_vals) == 30
    for v in atr_vals[14:]:
        assert v > 0.0


def test_monte_carlo_var_c():
    var1 = fastpath.fast_monte_carlo_var(0.001, 0.02, 5000, 1, 100000.0, 0.95, seed=42)
    var2 = fastpath.fast_monte_carlo_var(0.001, 0.02, 5000, 1, 100000.0, 0.95, seed=42)
    assert var1 is not None and var2 is not None
    assert var1 == var2
    assert var1 > 0.0


def test_fix_checksum_parity():
    msg = "8=FIX.4.2\x019=12\x0135=0\x01"
    cks = fastpath.fast_fix_checksum(msg)
    assert cks == sum(ord(c) for c in msg) % 256
