import json
import os
import sys
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live import LiveConnector, normalize_symbol_pair
from models import EventType, QualityStatus, RawEvent
from storage import Store
from pipeline import Pipeline
from bbo import BBOEngine


def test_symbol_normalization():
    """Verifies symbol pairs correctly resolve across exchanges."""
    canon, b_sym, c_sym = normalize_symbol_pair("BTCUSDT")
    assert canon == "BTC/USD"
    assert b_sym == "BTCUSDT"
    assert c_sym == "BTC-USD"

    canon, b_sym, c_sym = normalize_symbol_pair("ETH-USD")
    assert canon == "ETH/USD"
    assert b_sym == "ETHUSDT"
    assert c_sym == "ETH-USD"

    canon, b_sym, c_sym = normalize_symbol_pair("SOL")
    assert canon == "SOL/USD"
    assert b_sym == "SOLUSDT"
    assert c_sym == "SOL-USD"


def test_mock_binance_parsing():
    """Verifies Binance ticker JSON is correctly transformed into a valid RawEvent."""
    connector = LiveConnector()
    mock_data = json.dumps({
        "symbol": "BTCUSDT",
        "bidPrice": "70100.50",
        "bidQty": "2.5",
        "askPrice": "70101.50",
        "askQty": "4.0",
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        raw = connector.fetch_binance_quote("BTCUSDT")
        assert raw is not None
        assert raw.source == "BINANCE"
        assert raw.payload["instrument"] == "BTC/USD"
        assert raw.payload["event_type"] == "QUOTE"
        assert raw.payload["bid"] == 70100.50
        assert raw.payload["ask"] == 70101.50
        assert raw.payload["bid_size"] == 2.5
        assert raw.payload["ask_size"] == 4.0


def test_mock_coinbase_parsing():
    """Verifies Coinbase ticker JSON is correctly transformed into a valid RawEvent."""
    connector = LiveConnector()
    mock_data = json.dumps({
        "bid": "70102.00",
        "ask": "70103.00",
        "size": "0.75",
        "price": "70102.50",
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        raw = connector.fetch_coinbase_quote("BTC-USD")
        assert raw is not None
        assert raw.source == "COINBASE"
        assert raw.payload["instrument"] == "BTC/USD"
        assert raw.payload["bid"] == 70102.00
        assert raw.payload["ask"] == 70103.00
        assert raw.payload["bid_size"] == 0.75


def test_pipeline_integration_with_live_events():
    """Live events feed directly through pipeline and produce multi-venue BBO."""
    store = Store(":memory:")
    bbo = BBOEngine()
    pipeline = Pipeline(store, bbo=bbo)

    # Binance quote: 70,000 / 70,005
    raw_b = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD", "event_type": "QUOTE",
            "exchange_ts": 1000.0, "sequence": 1,
            "bid": 70000.0, "ask": 70005.0, "bid_size": 2.0, "ask_size": 3.0,
        },
        receive_timestamp=1000.002,
        raw_id="raw-b-1",
    )
    # Coinbase quote: 70,002 (tighter bid!) / 70,008
    raw_c = RawEvent(
        source="COINBASE",
        payload={
            "instrument": "BTC/USD", "event_type": "QUOTE",
            "exchange_ts": 1000.01, "sequence": 2,
            "bid": 70002.0, "ask": 70008.0, "bid_size": 1.5, "ask_size": 1.0,
        },
        receive_timestamp=1000.012,
        raw_id="raw-c-1",
    )

    pipeline.process_one(raw_b)
    pipeline.process_one(raw_c)
    pipeline.finish()

    # Verify BBO calculated synthetic top-of-book across Binance and Coinbase
    cur = bbo.current_bbo("BTC/USD")
    assert cur is not None
    assert cur.best_bid == 70002.0
    assert cur.best_bid_source == "COINBASE"
    assert cur.best_ask == 70005.0
    assert cur.best_ask_source == "BINANCE"
    assert cur.spread == 3.0
    assert cur.mid_price == 70003.5

    # Verify stored in SQLite
    bbos = store.query_bbo("BTC/USD")
    assert len(bbos) == 1
    assert bbos[0]["best_bid"] == 70002.0
    assert bbos[0]["best_ask"] == 70005.0

    store.close()


def test_live_connector_network_error_resilience():
    """Network timeouts or 5xx errors return None safely without unhandled crashes."""
    connector = LiveConnector(timeout=0.1)
    with mock.patch("urllib.request.urlopen", side_effect=Exception("Connection timed out")):
        assert connector.fetch_binance_quote("BTCUSDT") is None
        assert connector.fetch_coinbase_quote("BTC-USD") is None
