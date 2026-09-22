"""
Unit tests verifying error surfacing across MDRAP (eliminating silent swallows).
Ensures:
- SecurityManager raises RuntimeError if SQLite persistence fails.
- MarketDataDaemon tracks SHM write failures and surfaces them in telemetry.
- central config parser surfaces syntax errors to sys.stderr and uses defaults.
- WebSocket frame parsers return malformed RawEvents instead of silently dropping corrupt data.
"""
import io
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from security import SecurityManager
from service import MarketDataDaemon
from config import load_config, QualityConfig
from ws_feed import parse_binance_frame
from polygon_feed import parse_polygon_trade
from models import RawEvent


class FailingStoreMock:
    """Mock store that raises an error on API key persistence."""
    def save_api_key(self, *args, **kwargs):
        raise RuntimeError("Disk I/O failure on save")

    def revoke_api_key(self, *args, **kwargs):
        raise RuntimeError("Disk I/O failure on revoke")

    def load_api_keys(self):
        raise RuntimeError("Database corrupted on load")


def test_security_register_api_key_db_failure_raises():
    mock_store = FailingStoreMock()
    sec = SecurityManager(store=mock_store)

    with pytest.raises(RuntimeError, match="Failed to persist API key to storage"):
        sec.register_api_key(client_id="client_bad", tier="ENTERPRISE")

    # In-memory cache must be clean -- key should NOT exist
    assert not any(e.client_id == "client_bad" for e in sec._api_keys.values())


def test_security_revoke_api_key_db_failure_raises():
    mock_store = FailingStoreMock()
    sec = SecurityManager(store=None)  # Start without DB
    ent = sec.register_api_key(client_id="client_ok")
    token = ent.token

    # Attach failing store and attempt revocation
    sec.store = mock_store
    with pytest.raises(RuntimeError, match="Failed to persist API key revocation"):
        sec.revoke_api_key(token)


def test_daemon_stats_includes_shm_telemetry():
    daemon = MarketDataDaemon(db_path=":memory:", enable_shm=False)
    st = daemon.stats()
    assert "shm_enabled" in st
    assert st["shm_enabled"] is False
    assert "shm_errors" in st
    assert st["shm_errors"] == 0


def test_config_parse_failure_surfaced_to_stderr():
    stderr_capture = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("invalid: [yaml: broken: ::")
            temp_path = f.name
        try:
            cfg = load_config(temp_path)
            err_output = stderr_capture.getvalue()
            assert "WARNING" in err_output
            assert "Failed to parse config file" in err_output
            assert isinstance(cfg.quality, QualityConfig)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    finally:
        sys.stderr = old_stderr


def test_ws_feed_corrupt_frame_returns_malformed_event():
    # Binance book ticker with corrupt non-numeric bid price
    corrupt_frame = {
        "u": 12345,
        "s": "BTCUSDT",
        "b": "NOT_A_FLOAT",
        "B": "1.0",
        "a": "65000.00",
        "A": "1.0",
    }
    raw = parse_binance_frame(corrupt_frame, "BTC/USD")
    assert raw is not None
    assert isinstance(raw, RawEvent)
    assert raw.payload.get("is_malformed") is True
    assert "could not convert string to float" in raw.payload.get("error", "")


def test_polygon_feed_nonpositive_trade_returns_malformed_event():
    # Polygon trade with price = 0.0 (corrupt/invalid price)
    item = {
        "ev": "T",
        "sym": "AAPL",
        "p": 0.0,
        "s": 100.0,
        "t": 1625000000125,
    }
    raw = parse_polygon_trade(item)
    assert raw is not None
    assert isinstance(raw, RawEvent)
    assert raw.payload.get("is_malformed") is True
