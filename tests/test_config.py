import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import (
    PlatformConfig,
    QualityConfig,
    load_config,
    _simple_yaml_parse,
)
from models import CanonicalEvent, EventType
from quality import QualityEngine


def test_default_config_creation():
    """Verifies default PlatformConfig creates all typed sub-configs."""
    cfg = PlatformConfig()
    assert cfg.quality.staleness_threshold_s == 0.05
    assert cfg.quality.price_anomaly_stddev == 6.0
    assert cfg.bbo.quote_ttl_s == 2.0
    assert cfg.watchdog.silence_threshold_s == 2.0
    assert cfg.storage.batch_size == 500
    assert cfg.security.rate_limit_per_sec == 20000.0
    assert cfg.daemon.port == 9876


def test_config_yaml_loading():
    """Verifies loading the project's config.yaml parses all sections."""
    cfg = load_config()
    assert cfg.quality.price_window == 50
    assert "crypto" in cfg.quality.asset_classes
    assert "equities" in cfg.quality.asset_classes
    assert cfg.quality.asset_classes["crypto"]["staleness_threshold_s"] == 2.0
    assert cfg.quality.asset_classes["equities"]["staleness_threshold_s"] == 0.25


def test_asset_class_overrides():
    """Verifies QualityConfig.for_instrument applies appropriate overrides."""
    base_cfg = QualityConfig(
        staleness_threshold_s=0.05,
        price_anomaly_stddev=6.0,
        asset_classes={
            "crypto": {"staleness_threshold_s": 2.0, "price_anomaly_stddev": 7.0},
            "equities": {"staleness_threshold_s": 0.25, "price_anomaly_stddev": 3.5},
        },
    )

    btc_cfg = base_cfg.for_instrument("BTC/USD")
    assert btc_cfg.staleness_threshold_s == 2.0
    assert btc_cfg.price_anomaly_stddev == 7.0

    aapl_cfg = base_cfg.for_instrument("AAPL")
    assert aapl_cfg.staleness_threshold_s == 0.25
    assert aapl_cfg.price_anomaly_stddev == 3.5

    unknown_cfg = base_cfg.for_instrument("UNKNOWN_FX")
    assert unknown_cfg.staleness_threshold_s == 0.05
    assert unknown_cfg.price_anomaly_stddev == 6.0


def test_quality_engine_with_config():
    """Verifies QualityEngine respects config thresholds for crypto vs equities."""
    cfg = QualityConfig(
        staleness_threshold_s=0.05,
        asset_classes={
            "crypto": {"staleness_threshold_s": 2.0},
            "equities": {"staleness_threshold_s": 0.10},
        },
    )
    qe = QualityEngine(config=cfg)

    # 1. Crypto tick with 0.5s network delay (allowed under crypto 2.0s tolerance)
    crypto_evt = CanonicalEvent(
        event_id="e1",
        instrument_id="BTC/USD",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.50,
        processing_timestamp=1000.51,
        source="BINANCE",
        sequence_number=1,
        bid_price=50000.0,
        ask_price=50001.0,
    )
    res_c = qe.evaluate(crypto_evt)
    assert res_c.quality_status.value == "VALID"

    # 2. Equity tick with 0.5s network delay (stale under equity 0.10s tolerance)
    equity_evt = CanonicalEvent(
        event_id="e2",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.50,
        processing_timestamp=1000.51,
        source="EQUITIES",
        sequence_number=1,
        bid_price=220.0,
        ask_price=220.05,
    )
    res_e = qe.evaluate(equity_evt)
    assert res_e.quality_status.value == "SUSPICIOUS"
    assert "STALE" in res_e.reasons


def test_fallback_yaml_parser():
    """Verifies the pure-Python stdlib YAML fallback parser."""
    yaml_sample = """
quality:
  staleness_threshold_s: 0.15
  price_anomaly_stddev: 5.5
  asset_classes:
    crypto:
      staleness_threshold_s: 3.0
bbo:
  quote_ttl_s: 1.5
storage:
  wal_mode: true
    """
    parsed = _simple_yaml_parse(yaml_sample)
    assert parsed["quality"]["staleness_threshold_s"] == 0.15
    assert parsed["quality"]["price_anomaly_stddev"] == 5.5
    assert parsed["quality"]["asset_classes"]["crypto"]["staleness_threshold_s"] == 3.0
    assert parsed["bbo"]["quote_ttl_s"] == 1.5
    assert parsed["storage"]["wal_mode"] is True
