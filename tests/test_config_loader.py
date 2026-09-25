import pytest
from config_loader import (
    load_config,
    resolve_config,
    compute_config_hash,
    to_quality_config,
)

SAMPLE_CONFIG = {
    "defaults": {
        "staleness_threshold_s": 0.05,
        "price_anomaly_stddev": 6.0,
        "price_window": 50,
        "circuit_filter_pct": 0.10,
    },
    "venue": {
        "binance": {
            "staleness_threshold_s": 2.0,
            "price_anomaly_stddev": 5.0,
            "instrument_class": {
                "crypto": {
                    "staleness_threshold_s": 1.5,
                }
            },
            "instrument": {
                "BTCUSDT": {
                    "staleness_threshold_s": 0.5,
                    "price_anomaly_stddev": 4.0,
                }
            },
        }
    },
}


def test_config_resolution_ladder():
    # 1. Base defaults
    resolved, origins = resolve_config(SAMPLE_CONFIG)
    assert resolved["staleness_threshold_s"] == 0.05
    assert origins["staleness_threshold_s"] == "defaults"

    # 2. Venue override
    resolved_v, origins_v = resolve_config(SAMPLE_CONFIG, venue="binance")
    assert resolved_v["staleness_threshold_s"] == 2.0
    assert origins_v["staleness_threshold_s"] == "venue.binance"
    assert resolved_v["circuit_filter_pct"] == 0.10
    assert origins_v["circuit_filter_pct"] == "defaults"

    # 3. Instrument class override
    resolved_cls, origins_cls = resolve_config(
        SAMPLE_CONFIG, venue="binance", instrument_class="crypto"
    )
    assert resolved_cls["staleness_threshold_s"] == 1.5
    assert (
        origins_cls["staleness_threshold_s"] == "venue.binance.instrument_class.crypto"
    )
    assert resolved_cls["price_anomaly_stddev"] == 5.0
    assert origins_cls["price_anomaly_stddev"] == "venue.binance"

    # 4. Specific instrument override
    resolved_inst, origins_inst = resolve_config(
        SAMPLE_CONFIG, venue="binance", symbol="BTCUSDT"
    )
    assert resolved_inst["staleness_threshold_s"] == 0.5
    assert origins_inst["staleness_threshold_s"] == "venue.binance.instrument.BTCUSDT"
    assert resolved_inst["price_anomaly_stddev"] == 4.0
    assert origins_inst["price_anomaly_stddev"] == "venue.binance.instrument.BTCUSDT"


def test_hash_determinism():
    h1 = compute_config_hash(SAMPLE_CONFIG)
    h2 = compute_config_hash(SAMPLE_CONFIG)
    assert h1 == h2
    assert len(h1) == 64


def test_to_quality_config():
    resolved_inst, _ = resolve_config(SAMPLE_CONFIG, venue="binance", symbol="BTCUSDT")
    q_cfg = to_quality_config(resolved_inst)
    assert q_cfg.staleness_threshold_s == 0.5
    assert q_cfg.price_anomaly_stddev == 4.0
    assert q_cfg.price_window == 50
