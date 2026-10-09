"""Tests for Phase 3 Workstream F — Market-Data Ingress Adapter Architecture."""

import pytest
from mdrap.ingress import (
    AdapterState,
    BaseFeedAdapter,
    FeedAdapterConfig,
    IngressError,
    ReplayFeedAdapter,
)
from mdrap.models import EventType, QualityStatus


def test_ingress_config_validation():
    """Config validation rejects empty venue or invalid max_frame_bytes."""
    with pytest.raises(IngressError, match="non-empty venue name"):
        FeedAdapterConfig(venue="", feed_id="FEED1")

    with pytest.raises(IngressError, match="non-empty feed_id"):
        FeedAdapterConfig(venue="NASDAQ", feed_id="")

    with pytest.raises(IngressError, match="max_frame_bytes must be strictly positive"):
        FeedAdapterConfig(venue="NASDAQ", feed_id="FEED1", max_frame_bytes=-1)


def test_ingress_adapter_lifecycle():
    """Adapter follows CLOSED -> CONNECTING -> CONNECTED -> STREAMING -> CLOSED."""
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="TEST_VENUE", feed_id="CH1"),
        frames=[{"seq": 1, "sym": "AAPL", "px": 150.0, "sz": 10.0}],
    )
    assert adapter.state == AdapterState.CLOSED

    # Polling while closed raises IngressError
    with pytest.raises(IngressError, match="Cannot poll while adapter is in state"):
        adapter.poll()

    adapter.connect()
    assert adapter.state == AdapterState.CONNECTED

    raw = adapter.poll()
    assert raw is not None
    assert adapter.state == AdapterState.STREAMING

    adapter.disconnect()
    assert adapter.state == AdapterState.CLOSED


def test_ingress_gap_and_duplicate_detection():
    """Adapter accurately identifies sequence gaps and duplicates."""
    frames = [
        {"seq": 100, "sym": "MSFT", "px": 300.0, "sz": 5.0},
        {"seq": 101, "sym": "MSFT", "px": 301.0, "sz": 5.0},
        {"seq": 105, "sym": "MSFT", "px": 302.0, "sz": 5.0},  # Gap of 3 (102, 103, 104)
        {"seq": 105, "sym": "MSFT", "px": 302.0, "sz": 5.0},  # Duplicate 105
        {"seq": 106, "sym": "MSFT", "px": 303.0, "sz": 5.0},
    ]
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="TEST_EXCH", feed_id="DATA"),
        frames=frames,
    )
    adapter.connect()

    received = []
    while True:
        raw = adapter.poll()
        if not raw:
            break
        received.append(raw)

    assert len(received) == 5
    assert adapter.stats.gaps_detected == 1
    assert adapter.stats.missing_events_count == 3
    assert adapter.stats.duplicates_detected == 1
    assert adapter.stats.raw_frames_received == 5


def test_ingress_normalization():
    """Adapter normalizes raw frames into canonical events with venue identity preserved."""
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="BINANCE", feed_id="DEPTH", session_id="SESS_A"),
        frames=[
            {"seq": 1, "sym": "BTC-USDT", "px": 65000.50, "sz": 1.25, "type": "TRADE"},
            {"seq": 2, "sym": "ETH-USDT", "bid": 2500.0, "ask": 2500.5, "type": "QUOTE"},
        ],
    )
    adapter.connect()

    raw1 = adapter.poll()
    assert raw1 is not None
    canon1 = adapter.normalize(raw1)
    assert canon1.instrument_id == "BTC-USDT"
    assert canon1.event_type == EventType.TRADE
    assert canon1.price == 65000.50
    assert canon1.quantity == 1.25
    assert canon1.source == "BINANCE"
    assert canon1.quality_status == QualityStatus.VALID

    raw2 = adapter.poll()
    assert raw2 is not None
    canon2 = adapter.normalize(raw2)
    assert canon2.instrument_id == "ETH-USDT"
    assert canon2.event_type == EventType.QUOTE
    assert canon2.bid_price == 2500.0
    assert canon2.ask_price == 2500.5

    health = adapter.health()
    assert health["connected"] is True
    assert health["venue"] == "BINANCE"
    assert health["gaps_detected"] == 0
