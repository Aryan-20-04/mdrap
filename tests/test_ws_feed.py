"""
Unit and Integration Tests for MDRAP Multi-Venue Persistent WebSocket Feed Engine.
"""

import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ws_feed import (
    parse_binance_frame,
    parse_coinbase_frame,
    parse_kraken_frame,
    parse_okx_frame,
    parse_bybit_frame,
    parse_venue_frame,
    WebSocketFeedManager,
    HAS_WEBSOCKETS,
)
from models import EventType, QualityStatus, RawEvent


def test_parse_binance_depth_frame():
    """Verify Binance @depth5 JSON payload parsing."""
    frame = {
        "lastUpdateId": 1234567,
        "bids": [["65000.00", "1.500"], ["64995.00", "2.100"]],
        "asks": [["65005.00", "0.800"], ["65010.00", "3.200"]],
    }
    raw = parse_binance_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "BINANCE"
    p = raw.payload
    assert p["instrument"] == "BTC/USD"
    assert p["event_type"] == "QUOTE"
    assert p["bid"] == 65000.00
    assert p["bid_size"] == 1.500
    assert p["ask"] == 65005.00
    assert p["ask_size"] == 0.800
    assert len(p["bids"]) == 2
    assert len(p["asks"]) == 2


def test_parse_binance_book_ticker():
    """Verify Binance @bookTicker JSON payload parsing."""
    frame = {
        "u": 400900217,
        "s": "BTCUSDT",
        "b": "64800.50",
        "B": "4.250",
        "a": "64801.00",
        "A": "1.100",
    }
    raw = parse_binance_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "BINANCE"
    assert raw.payload["bid"] == 64800.50
    assert raw.payload["ask"] == 64801.00


def test_parse_coinbase_ticker_frame():
    """Verify Coinbase ticker JSON payload parsing."""
    frame = {
        "type": "ticker",
        "sequence": 987654,
        "product_id": "BTC-USD",
        "price": "65002.00",
        "best_bid": "65001.00",
        "best_bid_size": "2.5",
        "best_ask": "65003.00",
        "best_ask_size": "1.8",
        "time": "2026-09-04T06:00:00.000000Z",
    }
    raw = parse_coinbase_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "COINBASE"
    assert raw.payload["bid"] == 65001.00
    assert raw.payload["ask"] == 65003.00
    assert raw.payload["bid_size"] == 2.5
    assert raw.payload["ask_size"] == 1.8


def test_parse_coinbase_snapshot_frame():
    """Verify Coinbase snapshot depth payload parsing."""
    frame = {
        "type": "snapshot",
        "product_id": "BTC-USD",
        "bids": [["65000.00", "1.2"], ["64998.00", "0.5"]],
        "asks": [["65004.00", "2.0"], ["65006.00", "1.1"]],
    }
    raw = parse_coinbase_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "COINBASE"
    assert raw.payload["bid"] == 65000.00
    assert raw.payload["ask"] == 65004.00
    assert len(raw.payload["bids"]) == 2


def test_parse_kraken_book_frame():
    """Verify Kraken book-10 depth snapshot list-format frame."""
    frame = [
        342,
        {
            "bs": [["65002.5", "1.500", "1741000000.123"]],
            "as": [["65004.5", "2.300", "1741000000.123"]],
        },
        "book-10",
        "XBT/USD",
    ]
    raw = parse_kraken_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "KRAKEN"
    assert raw.payload["bid"] == 65002.5
    assert raw.payload["ask"] == 65004.5
    assert raw.payload["bid_size"] == 1.500
    assert raw.payload["ask_size"] == 2.300


def test_parse_okx_books5_frame():
    """Verify OKX books5 orderbook JSON frame."""
    frame = {
        "arg": {"channel": "books5", "instId": "BTC-USDT"},
        "data": [
            {
                "bids": [["65001.2", "3.0", "0", "4"], ["65000.0", "1.0", "0", "1"]],
                "asks": [["65003.8", "1.5", "0", "2"], ["65005.0", "2.0", "0", "3"]],
                "ts": "1741000000123",
            }
        ],
    }
    raw = parse_okx_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "OKX"
    assert raw.payload["bid"] == 65001.2
    assert raw.payload["ask"] == 65003.8
    assert len(raw.payload["bids"]) == 2


def test_parse_bybit_orderbook_frame():
    """Verify Bybit orderbook.5 JSON frame."""
    frame = {
        "topic": "orderbook.5.BTCUSDT",
        "type": "snapshot",
        "ts": 1741000000123,
        "data": {
            "s": "BTCUSDT",
            "b": [["64999.00", "5.0"], ["64995.00", "2.5"]],
            "a": [["65002.00", "1.0"], ["65006.00", "3.5"]],
        },
    }
    raw = parse_bybit_frame(frame, "BTC/USD")
    assert raw is not None
    assert raw.source == "BYBIT"
    assert raw.payload["bid"] == 64999.00
    assert raw.payload["ask"] == 65002.00


def test_parse_venue_frame_dispatcher():
    """Verify universal parse_venue_frame dispatcher with string JSON."""
    json_str = json.dumps(
        {
            "lastUpdateId": 999,
            "bids": [["65000.0", "1.0"]],
            "asks": [["65005.0", "2.0"]],
        }
    )
    raw = parse_venue_frame("BINANCE", json_str, "BTC/USD")
    assert raw is not None
    assert raw.source == "BINANCE"
    assert raw.payload["bid"] == 65000.0

    # Unknown venue returns None safely
    assert parse_venue_frame("UNKNOWN_EXCHANGE", json_str, "BTC/USD") is None


def test_ws_manager_lifecycle():
    """Verify WebSocketFeedManager configuration, stats tracking, and queue buffering."""
    manager = WebSocketFeedManager(symbols=["BTC/USD"], venues=["BINANCE", "COINBASE"])
    assert "BINANCE" in manager.venues
    assert "COINBASE" in manager.venues
    stats = manager.stats()
    assert "BINANCE" in stats
    assert "COINBASE" in stats
    assert stats["BINANCE"]["frames"] == 0

    # Verify queue buffering
    mock_raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "QUOTE",
            "bid": 65000.0,
            "ask": 65005.0,
        },
        receive_timestamp=1000.0,
        raw_id="r1",
    )
    manager._queue.put(mock_raw)

    events = list(manager.stream_events(limit=1, timeout_s=0.1))
    assert len(events) == 1
    assert events[0].source == "BINANCE"
