"""
Unit and Integration Tests for Polygon.io High-Throughput Streaming Feed Engine.
"""
import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from polygon_feed import (
    parse_polygon_quote,
    parse_polygon_trade,
    parse_polygon_aggregate,
    parse_polygon_frame,
    PolygonMockStream,
    PolygonFeedManager,
    POLYGON_EXCHANGE_MAP,
)
from models import RawEvent


def test_parse_polygon_quote():
    """Verify parsing of Polygon Quote ('Q') frame."""
    frame = {
        "ev": "Q",
        "sym": "AAPL",
        "bx": "V",
        "bp": 150.25,
        "bs": 10,
        "ax": "Q",
        "ap": 150.28,
        "as": 5,
        "c": 0,
        "t": 1625000000123,
    }
    ev = parse_polygon_quote(frame)
    assert ev is not None
    assert isinstance(ev, RawEvent)
    assert ev.source == "POLYGON-NASDAQ"
    p = ev.payload
    assert p["instrument"] == "AAPL"
    assert p["event_type"] == "QUOTE"
    assert p["bid"] == 150.25
    assert p["ask"] == 150.28
    assert p["bid_size"] == 10.0
    assert p["ask_size"] == 5.0
    assert p["bid_venue"] == "IEX"
    assert p["ask_venue"] == "NASDAQ"
    assert len(p["bids"]) == 1
    assert len(p["asks"]) == 1


def test_parse_polygon_trade():
    """Verify parsing of Polygon Trade ('T') frame."""
    frame = {
        "ev": "T",
        "sym": "MSFT",
        "i": "987654",
        "x": 4,
        "p": 380.50,
        "s": 200,
        "c": [14, 41],
        "t": 1625000000500,
    }
    ev = parse_polygon_trade(frame)
    assert ev is not None
    assert isinstance(ev, RawEvent)
    assert ev.source == "POLYGON-FINRA_TRF"
    p = ev.payload
    assert p["instrument"] == "MSFT"
    assert p["event_type"] == "TRADE"
    assert p["price"] == 380.50
    assert p["quantity"] == 200.0
    assert p["trade_id"] == "987654"
    assert p["exchange"] == "FINRA_TRF"


def test_parse_polygon_aggregate():
    """Verify parsing of Polygon Aggregate bar ('A') frame."""
    frame = {
        "ev": "A",
        "sym": "NVDA",
        "v": 1500,
        "op": 125.10,
        "vw": 125.25,
        "o": 125.15,
        "c": 125.30,
        "h": 125.40,
        "l": 125.05,
        "s": 1625000000000,
        "e": 1625000001000,
    }
    ev = parse_polygon_aggregate(frame)
    assert ev is not None
    assert ev.source == "POLYGON-AGG"
    p = ev.payload
    assert p["instrument"] == "NVDA"
    assert p["event_type"] == "TRADE"
    assert p["price"] == 125.30
    assert p["quantity"] == 1500.0
    assert p["open"] == 125.15
    assert p["close"] == 125.30


def test_parse_polygon_batch_frame():
    """Verify parsing of multi-message batch JSON string."""
    batch_json = json.dumps([
        {"ev": "status", "status": "success"},
        {"ev": "Q", "sym": "AAPL", "bx": "V", "bp": 150.0, "bs": 10, "ax": "Q", "ap": 150.1, "as": 20, "t": 1625000000000},
        {"ev": "T", "sym": "AAPL", "i": "1", "x": 7, "p": 150.05, "s": 100, "t": 1625000000001},
        {"invalid": "format"},
    ])
    events = parse_polygon_frame(batch_json)
    assert len(events) == 2
    assert events[0].payload["event_type"] == "QUOTE"
    assert events[1].payload["event_type"] == "TRADE"


def test_polygon_mock_stream():
    """Verify high-fidelity mock stream produces valid wire JSON frames."""
    mock = PolygonMockStream(["AAPL", "TSLA"], seed=123)
    frame_str = mock.generate_frame()
    assert isinstance(frame_str, str)
    events = parse_polygon_frame(frame_str)
    assert len(events) == 2
    assert events[0].payload["instrument"] in ("AAPL", "TSLA")
    assert events[1].payload["instrument"] in ("AAPL", "TSLA")


def test_polygon_feed_manager_mock():
    """Verify PolygonFeedManager operates in mock mode and emits events."""
    mgr = PolygonFeedManager(symbols=["AAPL", "NVDA"], mock_mode=True, max_queue_size=100)
    assert mgr.mock_mode is True
    mgr.start()
    assert mgr.is_running() is True

    events = []
    for ev in mgr.stream_events(limit=5, timeout_s=2.0):
        events.append(ev)

    mgr.stop()
    assert mgr.is_running() is False
    assert len(events) == 5
    for ev in events:
        assert ev.payload["instrument"] in ("AAPL", "NVDA")

    stats = mgr.stats()
    assert stats["events"] >= 5
    assert stats["mock_mode"] is True


def test_polygon_feed_manager_queue_eviction():
    """Verify bounded queue evicts oldest events to preserve real-time low latency."""
    mgr = PolygonFeedManager(symbols=["AAPL"], mock_mode=True, max_queue_size=5)
    mgr._stop_event.set()  # Don't start background loop

    for i in range(10):
        raw = RawEvent(
            source="TEST",
            payload={"instrument": "AAPL", "event_type": "QUOTE", "bid": 100 + i, "ask": 101 + i},
            receive_timestamp=time.time(),
            raw_id=f"test-{i}",
        )
        mgr._enqueue_event(raw)

    assert mgr._queue.qsize() == 5
    # Oldest events 0-4 should have been evicted; newest 5-9 remain
    ev = mgr._queue.get()
    assert ev.raw_id == "test-5"
