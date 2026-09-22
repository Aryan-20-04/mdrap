"""
Resilience & Offline Integration Tests for MarketDataDaemon, StreamClient & WebSocket Feeds.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from service import MarketDataDaemon, StreamClient
from ws_feed import (
    parse_binance_frame,
    parse_bybit_frame,
    parse_coinbase_frame,
    parse_kraken_frame,
    parse_okx_frame,
)


# ---------------------------------------------------------------------------
# Offline WebSocket Frame Parsing Tests (Zero-Network)
# ---------------------------------------------------------------------------

def test_offline_binance_frame_parser():
    """Verify Binance WebSocket JSON depth and trade frames parse accurately into RawEvent."""
    frame_ticker = {
        "s": "BTCUSDT",
        "b": "65000.50",
        "B": "1.250",
        "a": "65001.00",
        "A": "2.500",
        "E": 1700000000123,
    }
    raw = parse_binance_frame(frame_ticker, "BTC/USD")
    assert raw is not None
    assert raw.source == "BINANCE"
    assert raw.payload["instrument"] == "BTC/USD"
    assert raw.payload["bid"] == 65000.50
    assert raw.payload["bid_size"] == 1.250
    assert raw.payload["ask"] == 65001.00
    assert raw.payload["ask_size"] == 2.500


def test_offline_coinbase_frame_parser():
    """Verify Coinbase WebSocket ticker frame parses into RawEvent."""
    frame_cb = {
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "65000.00",
        "best_bid": "64999.50",
        "best_bid_size": "0.75",
        "best_ask": "65000.50",
        "best_ask_size": "1.10",
        "time": "2026-09-05T12:00:00.000000Z",
    }
    raw = parse_coinbase_frame(frame_cb, "BTC/USD")
    assert raw is not None
    assert raw.source == "COINBASE"
    assert raw.payload["bid"] == 64999.50
    assert raw.payload["ask"] == 65000.50


def test_offline_kraken_okx_bybit_parsers():
    """Verify Kraken, OKX, and Bybit payload frames parse cleanly."""
    # Kraken ticker frame (list with dict body)
    frame_kr = [0, {"b": ["65000.0", "1", "0.5"], "a": ["65001.0", "1", "1.0"]}, "ticker", "XBT/USD"]
    raw_kr = parse_kraken_frame(frame_kr, "BTC/USD")
    assert raw_kr is not None
    assert raw_kr.source == "KRAKEN"
    assert raw_kr.payload["bid"] == 65000.0
    assert raw_kr.payload["ask"] == 65001.0

    # OKX books5 frame
    frame_okx = {
        "arg": {"channel": "books5", "instId": "BTC-USDT"},
        "data": [{"bids": [["65000.0", "1.0"]], "asks": [["65001.0", "2.0"]]}],
    }
    raw_okx = parse_okx_frame(frame_okx, "BTC/USD")
    assert raw_okx is not None
    assert raw_okx.source == "OKX"
    assert raw_okx.payload["bid"] == 65000.0
    assert raw_okx.payload["ask"] == 65001.0

    # Bybit orderbook frame
    frame_bybit = {
        "topic": "orderbook.5.BTCUSDT",
        "data": {"b": [["65000.0", "1.5"]], "a": [["65001.0", "2.5"]]},
    }
    raw_bb = parse_bybit_frame(frame_bybit, "BTC/USD")
    assert raw_bb is not None
    assert raw_bb.source == "BYBIT"
    assert raw_bb.payload["bid"] == 65000.0


# ---------------------------------------------------------------------------
# MarketDataDaemon & StreamClient Resilience Tests
# ---------------------------------------------------------------------------

def test_daemon_client_connect_and_broadcast():
    """Verify StreamClient connects, authenticates, receives broadcast ticks, and closes cleanly."""
    port = 19877
    daemon = MarketDataDaemon(host="127.0.0.1", port=port, db_path=":memory:", enable_shm=False, use_live=False, sim_speed_eps=5000.0)
    daemon.start(blocking=False)
    time.sleep(0.3)

    try:
        client = StreamClient(host="127.0.0.1", port=port, auth_token="mdrap_demo_pro_key")
        client.connect()

        # Query daemon status over client
        st = client.get_status()
        assert isinstance(st, dict)
        assert st["port"] == port
        assert st["active_clients"] >= 1

        # Stream 3 ticks with explicit limit
        ticks = list(client.stream(symbol="ALL", limit=3))
        assert len(ticks) == 3
        for t in ticks:
            assert t.get("type") == "TICK"
            assert "sym" in t

        client.close()
    finally:
        daemon.stop()
