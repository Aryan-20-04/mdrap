"""
Unit tests for ML/AI Feature Store and Technical Indicators (Gap 9).
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import math
import pytest
from features import (
    sma,
    ema,
    rsi,
    macd,
    bollinger_bands,
    atr,
    obv,
    vpin,
    order_book_imbalance,
    realized_volatility,
    FeatureRegistry,
    FeatureStore,
)


def test_sma_calculation():
    prices = [10.0, 20.0, 30.0, 40.0, 50.0]
    res = sma(prices, 3)
    assert math.isnan(res[0])
    assert math.isnan(res[1])
    assert res[2] == 20.0
    assert res[3] == 30.0
    assert res[4] == 40.0


def test_ema_calculation():
    prices = [10.0, 11.0, 12.0, 13.0, 14.0]
    res = ema(prices, 3)
    assert math.isnan(res[0])
    assert math.isnan(res[1])
    assert abs(res[2] - 11.0) < 1e-6
    # Multiplier = 2 / 4 = 0.5; (13 - 11) * 0.5 + 11 = 12.0
    assert abs(res[3] - 12.0) < 1e-6


def test_rsi_bounds():
    prices = [100.0 + i for i in range(30)]
    r = rsi(prices, 14)
    # Strictly increasing prices -> RSI should be high (close to 100)
    valid_vals = [x for x in r if not math.isnan(x)]
    assert len(valid_vals) > 0
    assert valid_vals[-1] >= 90.0


def test_macd_computation():
    prices = [100.0 + (i % 5) for i in range(50)]
    macd_line, signal_line, hist = macd(prices, fast=12, slow=26, signal=9)
    assert len(macd_line) == len(prices)
    assert len(signal_line) == len(prices)
    assert len(hist) == len(prices)


def test_bollinger_bands():
    prices = [100.0 + (i % 3) for i in range(30)]
    upper, middle, lower = bollinger_bands(prices, period=20, num_std=2.0)
    valid_idx = [i for i, v in enumerate(middle) if not math.isnan(v)]
    for idx in valid_idx:
        assert upper[idx] >= middle[idx] >= lower[idx]


def test_atr_and_obv():
    highs = [105.0, 107.0, 106.0, 108.0]
    lows = [95.0, 96.0, 97.0, 98.0]
    closes = [100.0, 102.0, 101.0, 107.0]
    volumes = [1000.0, 1500.0, 800.0, 2000.0]

    atr_vals = atr(highs, lows, closes, period=3)
    assert len(atr_vals) == 4

    obv_vals = obv(closes, volumes)
    assert len(obv_vals) == 4
    assert obv_vals[0] == 1000.0
    assert obv_vals[1] == 2500.0  # 102 > 100 -> +1500
    assert obv_vals[2] == 1700.0  # 101 < 102 -> -800
    assert obv_vals[3] == 3700.0  # 107 > 101 -> +2000


def test_microstructure_features():
    # VPIN
    trades = [(100.0, 100.0), (101.0, 200.0), (99.0, 300.0)]
    v = vpin(trades, bucket_volume=200.0)
    assert 0.0 <= v <= 1.0

    # Order book imbalance
    bids = [(99.0, 500.0), (98.0, 300.0)]
    asks = [(101.0, 200.0), (102.0, 100.0)]
    imbalance = order_book_imbalance(bids, asks, levels=2)
    # (800 - 300) / 1100 = 500 / 1100 ~ 0.4545
    assert abs(imbalance - (500.0 / 1100.0)) < 1e-4

    # Realized volatility
    rv = realized_volatility([100.0, 102.0, 101.0, 103.0, 102.0])
    assert rv > 0.0


def test_feature_store_integration():
    bars = []
    for i in range(40):
        bars.append(
            {
                "timestamp": 1000.0 + i * 60,
                "open": 100.0 + i,
                "high": 102.0 + i,
                "low": 99.0 + i,
                "close": 101.0 + i,
                "volume": 1000.0 + i * 10,
            }
        )

    fs = FeatureStore()
    feat_dict = fs.compute_features(bars)
    assert "rsi_14" in feat_dict
    assert "bb_upper" in feat_dict
    assert "atr_14" in feat_dict
    assert "obv" in feat_dict

    records = fs.to_records(bars)
    assert len(records) == 40
    assert "close" in records[-1]
    assert "rsi_14" in records[-1]
    assert not math.isnan(records[-1]["rsi_14"])


def test_python_fallbacks_without_fastpath(monkeypatch):
    import features

    monkeypatch.setattr(features, "fastpath", None)

    prices = [100.0 + (i * 0.5) for i in range(35)]
    # EMA fallback
    res_ema = features.ema(prices, 5)
    assert len(res_ema) == len(prices)
    assert not math.isnan(res_ema[4])

    # RSI fallback
    res_rsi = features.rsi(prices, 14)
    assert len(res_rsi) == len(prices)
    assert not math.isnan(res_rsi[14])

    # Bollinger fallback
    upper, mid, lower = features.bollinger_bands(prices, period=10, num_std=2.0)
    assert len(upper) == len(prices)
    assert upper[-1] >= mid[-1] >= lower[-1]

    # ATR fallback
    highs = [p + 2.0 for p in prices]
    lows = [p - 2.0 for p in prices]
    closes = prices
    res_atr = features.atr(highs, lows, closes, period=14)
    assert len(res_atr) == len(prices)
    assert not math.isnan(res_atr[14])


def test_features_empty_and_edge_cases():
    import features

    # Empty inputs
    assert features.rsi([]) == []
    assert features.obv([], []) == []
    assert math.isnan(features.vpin([]))
    assert features.order_book_imbalance([], []) == 0.0
    assert math.isnan(features.realized_volatility([]))
    assert math.isnan(features.realized_volatility([100.0]))
    assert features.realized_volatility([100.0, 100.0]) == 0.0

    # Constant price (zero loss / zero gain in RSI)
    flat_prices = [100.0] * 20
    flat_rsi = features.rsi(flat_prices, 10)
    assert flat_rsi[-1] == 100.0

    # FeatureStore with empty bars
    fs = features.FeatureStore()
    assert fs.to_records([]) == []


def test_feature_registry_custom():
    import features

    reg = features.FeatureRegistry()
    reg.register(
        "custom_feat", lambda b: [1.0] * len(b), description="Constant feature"
    )
    assert reg.get("custom_feat") is not None
    assert reg.get("non_existent") is None
    all_f = reg.list_all()
    assert len(all_f) == 1
    assert all_f[0]["name"] == "custom_feat"
