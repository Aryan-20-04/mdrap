import json
import os
import sys
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live import LiveConnector, normalize_symbol_pair, resolve_venue_symbols
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


def test_resolve_venue_symbols():
    """Verifies multi-venue symbol mapping for crypto and equities."""
    btc = resolve_venue_symbols("BTC")
    assert btc["canonical"] == "BTC/USD"
    assert btc["type"] == "CRYPTO"
    assert btc["binance"] == "BTCUSDT"
    assert btc["coinbase"] == "BTC-USD"
    assert btc["kraken"] == "XBTUSD"
    assert btc["okx"] == "BTC-USDT"
    assert btc["bybit"] == "BTCUSDT"

    aapl = resolve_venue_symbols("AAPL")
    assert aapl["canonical"] == "AAPL"
    assert aapl["type"] == "EQUITY"
    assert aapl["yahoo"] == "AAPL"

    gold = resolve_venue_symbols("GOLD")
    assert gold["canonical"] == "GOLD"
    assert gold["yahoo"] == "GC=F"


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


def test_mock_kraken_parsing():
    """Verifies Kraken ticker JSON is correctly transformed into a valid RawEvent."""
    connector = LiveConnector()
    mock_data = json.dumps({
        "result": {
            "XXBTZUSD": {
                "a": ["70104.00", "1", "1.5"],
                "b": ["70101.00", "2", "2.0"],
                "c": ["70102.50", "0.5"],
            }
        }
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        raw = connector.fetch_kraken_quote("BTC")
        assert raw is not None
        assert raw.source == "KRAKEN"
        assert raw.payload["instrument"] == "BTC/USD"
        assert raw.payload["bid"] == 70101.00
        assert raw.payload["ask"] == 70104.00
        assert raw.payload["bid_size"] == 2.0
        assert raw.payload["ask_size"] == 1.5


def test_mock_okx_parsing():
    """Verifies OKX ticker JSON is correctly transformed into a valid RawEvent."""
    connector = LiveConnector()
    mock_data = json.dumps({
        "data": [
            {
                "instId": "BTC-USDT",
                "bidPx": "70103.50",
                "askPx": "70104.50",
                "bidSz": "3.2",
                "askSz": "1.8",
            }
        ]
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        raw = connector.fetch_okx_quote("BTC")
        assert raw is not None
        assert raw.source == "OKX"
        assert raw.payload["instrument"] == "BTC/USD"
        assert raw.payload["bid"] == 70103.50
        assert raw.payload["ask"] == 70104.50
        assert raw.payload["bid_size"] == 3.2


def test_mock_bybit_parsing():
    """Verifies Bybit ticker JSON is correctly transformed into a valid RawEvent."""
    connector = LiveConnector()
    mock_data = json.dumps({
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "bid1Price": "70105.00",
                    "ask1Price": "70106.00",
                    "bid1Size": "5.0",
                    "ask1Size": "4.5",
                }
            ]
        }
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        raw = connector.fetch_bybit_quote("BTC")
        assert raw is not None
        assert raw.source == "BYBIT"
        assert raw.payload["instrument"] == "BTC/USD"
        assert raw.payload["bid"] == 70105.00
        assert raw.payload["ask"] == 70106.00


def test_mock_equity_parsing():
    """Verifies Yahoo Finance equity quote is transformed into a valid RawEvent."""
    connector = LiveConnector()
    mock_data = json.dumps({
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 225.50,
                        "bid": 225.48,
                        "ask": 225.52,
                        "regularMarketVolume": 5000000,
                    }
                }
            ]
        }
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        raw = connector.fetch_equity_quote("AAPL")
        assert raw is not None
        assert raw.source == "EQUITIES"
        assert raw.payload["instrument"] == "AAPL"
        assert raw.payload["bid"] == 225.48
        assert raw.payload["ask"] == 225.52


def test_5_venue_consolidated_nbbo():
    """Live events feed from 5 venues directly through pipeline and produce multi-venue BBO."""
    store = Store(":memory:")
    bbo = BBOEngine()
    pipeline = Pipeline(store, bbo=bbo)

    # 1. Binance: Bid 70,000 / Ask 70,008
    raw_b = RawEvent(
        source="BINANCE",
        payload={"instrument": "BTC/USD", "event_type": "QUOTE", "exchange_ts": 1000.0, "sequence": 1,
                 "bid": 70000.0, "ask": 70008.0, "bid_size": 2.0, "ask_size": 3.0},
        receive_timestamp=1000.002, raw_id="raw-b-1",
    )
    # 2. Coinbase: Bid 70,002 / Ask 70,007
    raw_c = RawEvent(
        source="COINBASE",
        payload={"instrument": "BTC/USD", "event_type": "QUOTE", "exchange_ts": 1000.01, "sequence": 2,
                 "bid": 70002.0, "ask": 70007.0, "bid_size": 1.5, "ask_size": 1.0},
        receive_timestamp=1000.012, raw_id="raw-c-1",
    )
    # 3. Kraken: Bid 70,001 / Ask 70,004 (tightest ask!)
    raw_k = RawEvent(
        source="KRAKEN",
        payload={"instrument": "BTC/USD", "event_type": "QUOTE", "exchange_ts": 1000.02, "sequence": 3,
                 "bid": 70001.0, "ask": 70004.0, "bid_size": 1.0, "ask_size": 2.5},
        receive_timestamp=1000.022, raw_id="raw-k-1",
    )
    # 4. OKX: Bid 70,003 (tightest bid!) / Ask 70,009
    raw_o = RawEvent(
        source="OKX",
        payload={"instrument": "BTC/USD", "event_type": "QUOTE", "exchange_ts": 1000.03, "sequence": 4,
                 "bid": 70003.0, "ask": 70009.0, "bid_size": 4.0, "ask_size": 2.0},
        receive_timestamp=1000.032, raw_id="raw-o-1",
    )
    # 5. Bybit: Bid 70,002 / Ask 70,006
    raw_by = RawEvent(
        source="BYBIT",
        payload={"instrument": "BTC/USD", "event_type": "QUOTE", "exchange_ts": 1000.04, "sequence": 5,
                 "bid": 70002.0, "ask": 70006.0, "bid_size": 3.0, "ask_size": 1.5},
        receive_timestamp=1000.042, raw_id="raw-by-1",
    )

    for r in [raw_b, raw_c, raw_k, raw_o, raw_by]:
        pipeline.process_one(r)
    pipeline.finish()

    # Verify BBO calculated synthetic top-of-book across all 5 venues
    cur = bbo.current_bbo("BTC/USD")
    assert cur is not None
    # Best Bid was 70,003 from OKX
    assert cur.best_bid == 70003.0
    assert cur.best_bid_source == "OKX"
    # Best Ask was 70,004 from KRAKEN
    assert cur.best_ask == 70004.0
    assert cur.best_ask_source == "KRAKEN"
    assert cur.spread == 1.0
    assert cur.mid_price == 70003.5
    assert not cur.is_crossed

    store.close()


def test_live_connector_network_error_resilience():
    """Network timeouts or 5xx errors return None safely without unhandled crashes."""
    connector = LiveConnector(timeout=0.1)
    with mock.patch("urllib.request.urlopen", side_effect=Exception("Connection timed out")):
        assert connector.fetch_binance_quote("BTCUSDT") is None
        assert connector.fetch_coinbase_quote("BTC-USD") is None
        assert connector.fetch_kraken_quote("BTC") is None
        assert connector.fetch_okx_quote("BTC") is None
        assert connector.fetch_bybit_quote("BTC") is None
        assert connector.fetch_equity_quote("AAPL") is None
        assert connector.fetch_equity_events("NNOX") == []
        assert connector.fetch_equity_candles("NNOX") == []


def test_dynamic_arbitrary_equity_resolution():
    """Verify that arbitrary stocks/equities (e.g. NNOX, PLTR, AMD, BRK.B) dynamically resolve as EQUITY."""
    for ticker, expected_yahoo in [("NNOX", "NNOX"), ("PLTR", "PLTR"), ("AMD", "AMD"), ("BRK.B", "BRK-B"), ("GME", "GME")]:
        info = resolve_venue_symbols(ticker)
        assert info["canonical"] == ticker.upper()
        assert info["type"] == "EQUITY"
        assert info["yahoo"] == expected_yahoo
        assert info["binance"] == ""
        assert info["coinbase"] == ""


def test_mock_equity_events_and_candles():
    """Verify that fetch_equity_events and fetch_equity_candles properly parse responses."""
    connector = LiveConnector()
    mock_chart_data = json.dumps({
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 0.83,
                        "bid": 0.82,
                        "ask": 0.84,
                        "regularMarketVolume": 150000,
                    },
                    "timestamp": [1700000000, 1700000060],
                    "indicators": {
                        "quote": [
                            {
                                "open": [0.81, 0.82],
                                "high": [0.84, 0.85],
                                "low": [0.80, 0.81],
                                "close": [0.82, 0.83],
                                "volume": [50000, 100000],
                            }
                        ]
                    }
                }
            ]
        }
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_chart_data
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # 1. Test events (both quote and trade)
        events = connector.fetch_equity_events("NNOX")
        assert len(events) == 2
        q_evt, t_evt = events[0], events[1]

        assert q_evt.source == "EQUITIES"
        assert q_evt.payload["instrument"] == "NNOX"
        assert q_evt.payload["event_type"] == "QUOTE"
        assert q_evt.payload["bid"] == 0.82
        assert q_evt.payload["ask"] == 0.84
        assert len(q_evt.payload["bids"]) == 5
        assert len(q_evt.payload["asks"]) == 5

        assert t_evt.source == "EQUITIES"
        assert t_evt.payload["instrument"] == "NNOX"
        assert t_evt.payload["event_type"] == "TRADE"
        assert t_evt.payload["price"] == 0.83

        # 2. Test candles
        candles = connector.fetch_equity_candles("NNOX", limit=10)
        assert len(candles) == 2
        assert candles[0]["instrument_id"] == "NNOX"
        assert candles[0]["open"] == 0.81
        assert candles[0]["high"] == 0.84
        assert candles[0]["low"] == 0.80
        assert candles[0]["close"] == 0.82
        assert candles[0]["volume"] == 50000.0
        assert candles[1]["close"] == 0.83
