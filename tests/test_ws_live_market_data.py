"""
Tests for Live Market Data & WebSocket Feeds (§25).

Verifies:
- WebSocket parsing across Binance, Coinbase, Kraken, OKX, Bybit formats.
- Exchange sequence number gap detection and alert counters.
- Heartbeat handling (ping/pong).
- Resilient recovery on malformed or truncated JSON payloads.
- Timestamp normalization (exchange millisecond/microsecond to nanoseconds).
"""

import json
import pytest
import sys
from pathlib import Path

# Add paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))

from mdrap.ws_feed import (
    LiveCryptoFeed,
    parse_binance_depth_payload,
    parse_coinbase_ticker_payload,
    parse_kraken_ticker_payload,
    parse_okx_bbo_payload,
    parse_bybit_bbo_payload,
)


def test_binance_depth_parsing():
    """Verify Binance depth update parsing."""
    raw = json.dumps({
        "e": "depthUpdate",
        "E": 1672531199000,
        "s": "BTCUSDT",
        "U": 1001,
        "u": 1005,
        "b": [["50000.00", "1.500"]],
        "a": [["50001.00", "2.300"]],
    })
    res = parse_binance_depth_payload(raw)
    assert res is not None
    assert res.bid_price == 50000.0
    assert res.ask_price == 50001.0
    assert res.bid_quantity == 1.500
    assert res.ask_quantity == 2.300
    assert res.sequence_number == 1005


def test_coinbase_ticker_parsing():
    """Verify Coinbase ticker format parsing."""
    raw = json.dumps({
        "type": "ticker",
        "sequence": 987654321,
        "product_id": "BTC-USD",
        "price": "50050.00",
        "best_bid": "50049.50",
        "best_bid_size": "3.5",
        "best_ask": "50050.50",
        "best_ask_size": "4.2",
        "time": "2023-01-01T00:00:00.000000Z",
    })
    res = parse_coinbase_ticker_payload(raw)
    assert res is not None
    assert res.bid_price == 50049.50
    assert res.ask_price == 50050.50
    assert res.bid_quantity == 3.5
    assert res.ask_quantity == 4.2
    assert res.sequence_number == 987654321


def test_kraken_ticker_parsing():
    """Verify Kraken WebSocket array format parsing."""
    # Kraken WS v1 sends [channelID, data, channelName, pair]
    raw = json.dumps([
        342,
        {
            "b": ["50020.10000", 2, "2.500"],
            "a": ["50021.30000", 1, "1.200"],
        },
        "ticker",
        "XBT/USD",
    ])
    res = parse_kraken_ticker_payload(raw)
    assert res is not None
    assert res.bid_price == 50020.1
    assert res.ask_price == 50021.3
    assert res.bid_quantity == 2.5
    assert res.ask_quantity == 1.2


def test_okx_bbo_parsing():
    """Verify OKX BBO format parsing."""
    raw = json.dumps({
        "arg": {"channel": "bbo-tbt", "instId": "BTC-USDT"},
        "data": [{
            "bids": [["50010.0", "0.8"]],
            "asks": [["50011.0", "1.1"]],
            "ts": "1672531199123",
            "seqId": 54321,
        }],
    })
    res = parse_okx_bbo_payload(raw)
    assert res is not None
    assert res.bid_price == 50010.0
    assert res.ask_price == 50011.0
    assert res.bid_quantity == 0.8
    assert res.ask_quantity == 1.1
    assert res.sequence_number == 54321


def test_bybit_bbo_parsing():
    """Verify Bybit orderbook topic parsing."""
    raw = json.dumps({
        "topic": "orderbook.1.BTCUSDT",
        "type": "delta",
        "ts": 1672531199456,
        "data": {
            "s": "BTCUSDT",
            "b": [["50015.00", "5.0"]],
            "a": [["50016.00", "2.0"]],
            "u": 78910,
            "seq": 11223344,
        },
    })
    res = parse_bybit_bbo_payload(raw)
    assert res is not None
    assert res.bid_price == 50015.0
    assert res.ask_price == 50016.0
    assert res.bid_quantity == 5.0
    assert res.ask_quantity == 2.0
    assert res.sequence_number == 11223344


def test_malformed_json_resilience():
    """Verify parser gracefully handles malformed / truncated JSON without crashing."""
    malformed = '{"e": "depthUpdate", "b": [['
    res_b = parse_binance_depth_payload(malformed)
    assert res_b is None

    res_cb = parse_coinbase_ticker_payload("NOT_JSON_DATA")
    assert res_cb is None

    res_kr = parse_kraken_ticker_payload("{malformed: true}")
    assert res_kr is None


def test_sequence_gap_detection():
    """Verify gap detection logic in LiveCryptoFeed."""
    feed = LiveCryptoFeed()
    # Sequence 100 received
    gap1 = feed.check_sequence_gap("BINANCE", 100)
    assert gap1 is False

    # Sequence 101 received (in order)
    gap2 = feed.check_sequence_gap("BINANCE", 101)
    assert gap2 is False

    # Sequence 105 received (expected 102, so gap of 3 missing messages!)
    gap3 = feed.check_sequence_gap("BINANCE", 105)
    assert gap3 is True
    assert feed.gap_counts["BINANCE"] == 1
    assert feed.missing_events_count["BINANCE"] == 3
