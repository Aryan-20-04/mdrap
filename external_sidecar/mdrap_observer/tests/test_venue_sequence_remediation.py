"""Comprehensive tests for Venue Sequence Propagation Remediation.

Validates:
- Preserving two independent sequence domains: transport stream seq vs venue-native seq
- Transmission of venue sequence through MarketDataDaemon TCP payload serialization
- Explicit diagnostic tracking when venue sequence is absent (feed_seq_unavailable)
- Supporting sequence number 0 when permitted by upstream protocols
- Duplicate, gap, out-of-order, reset, and snapshot semantics
- Transport gap with continuous venue sequence
- Venue gap with continuous transport sequence
"""

import os
import sys
import time
import pytest

# Ensure mdrap and observer in sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mdrap.models import RawEvent
from mdrap.service import MarketDataDaemon
from observer.state import (
    ChannelSequenceState,
    ObserverState,
    SequenceDisposition,
    StreamHealth,
    VenueHealth,
)


def test_venue_sequence_propagation_through_daemon_tcp_payload(monkeypatch):
    """Verify that MarketDataDaemon._process_and_broadcast() serializes feed_seq into payload."""
    monkeypatch.setenv("MDRAP_API_KEY_SALT", "test_salt_12345")
    daemon = MarketDataDaemon(port=0, enable_shm=False)

    # Raw event with explicit sequence 1050
    raw = RawEvent(
        source="BINANCE",
        receive_timestamp=time.time(),
        receive_timestamp_ns=time.perf_counter_ns(),
        payload={
            "instrument": "BTC-USDT",
            "event_type": "TICK",
            "price": 65000.0,
            "bid": 64999.0,
            "ask": 65001.0,
            "sequence": 1050,
        },
    )

    # Process and capture broadcast payload
    captured_payload = None
    original_record_replay = daemon._record_replay

    def mock_record_replay(p):
        nonlocal captured_payload
        captured_payload = p
        original_record_replay(p)

    daemon._record_replay = mock_record_replay
    daemon._process_and_broadcast(raw)

    assert captured_payload is not None
    # Transport sequence: monotonic daemon broadcast ID (1)
    assert captured_payload["seq"] == 1
    # Venue sequence: propagated from raw payload (1050)
    assert captured_payload["feed_seq"] == 1050
    assert captured_payload["venue_seq"] == 1050
    # Nanosecond monotonic latency fields present
    assert captured_payload["t0_ns"] > 0
    assert captured_payload["broadcast_ts_ns"] >= captured_payload["t0_ns"]
    assert captured_payload["engine_ns"] >= 0


def test_missing_venue_sequence_is_not_silently_zeroed():
    """Verify that absent venue sequence increments feed_seq_unavailable diagnostic."""
    state = ObserverState(subscription_filter="ALL")

    # Event with transport seq 1, but NO venue sequence
    event = {
        "type": "TICK",
        "seq": 1,
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "price": 64000.0,
    }

    state.process_event(event)

    health = state.health["COINBASE"]
    assert health.events_received == 1
    # Missing sequence must increment feed_seq_unavailable diagnostic
    assert health.feed_seq_unavailable == 1
    # Gaps, duplicates, and out-of-order must NOT be flagged
    assert health.gaps == 0
    assert health.duplicates == 0
    assert health.out_of_order == 0
    # Last feed seq must remain -1 (uninitialized)
    assert health.last_feed_seq == -1
    assert health.has_sequence_feed is False


def test_sequence_number_zero_supported():
    """Verify that venue sequence number 0 is supported and not conflated with None."""
    state = ObserverState(subscription_filter="ALL")

    # ITCH/Exchange frame starting at sequence 0
    event0 = {
        "type": "TICK",
        "seq": 1,
        "feed_seq": 0,
        "venue": "BINANCE",
        "symbol": "BTC-USDT",
    }
    state.process_event(event0)

    health = state.health["BINANCE"]
    assert health.events_received == 1
    assert health.feed_seq_unavailable == 0
    assert health.has_sequence_feed is True
    assert health.last_feed_seq == 0
    assert health.gaps == 0

    # Follow-up sequence 1
    event1 = {
        "type": "TICK",
        "seq": 2,
        "feed_seq": 1,
        "venue": "BINANCE",
        "symbol": "BTC-USDT",
    }
    state.process_event(event1)
    assert health.events_received == 2
    assert health.last_feed_seq == 1
    assert health.gaps == 0


def test_venue_sequence_gap_duplicate_and_out_of_order():
    """Verify per-venue gap, duplicate, and out-of-order accounting."""
    state = ObserverState(subscription_filter="ALL")
    venue = "KRAKEN"

    # Seq 100
    state.process_event(
        {"seq": 1, "feed_seq": 100, "venue": venue, "symbol": "BTC-USD"}
    )
    h = state.health[venue]
    assert h.last_feed_seq == 100
    assert h.gaps == 0
    assert h.duplicates == 0

    # Duplicate: Seq 100 again
    state.process_event(
        {"seq": 2, "feed_seq": 100, "venue": venue, "symbol": "BTC-USD"}
    )
    assert h.duplicates == 1

    # Forward gap: Seq 105 (missing 101, 102, 103, 104 -> gap of 4)
    state.process_event(
        {"seq": 3, "feed_seq": 105, "venue": venue, "symbol": "BTC-USD"}
    )
    assert h.gaps == 4
    assert h.last_feed_seq == 105

    # Out-of-order: Seq 103 arriving late (< 105)
    state.process_event(
        {"seq": 4, "feed_seq": 103, "venue": venue, "symbol": "BTC-USD"}
    )
    assert h.out_of_order == 1


def test_transport_gap_with_continuous_venue_sequence():
    """Verify that a network transport gap does not affect independent venue continuity."""
    state = ObserverState(subscription_filter="ALL")
    venue = "KRAKEN"

    # Transport seq 1 -> venue seq 50
    state.process_event({"seq": 1, "feed_seq": 50, "venue": venue, "symbol": "BTC-USD"})

    # Network drops transport seq 2, 3, 4; transport seq 5 arrives with venue seq 51!
    state.process_event({"seq": 5, "feed_seq": 51, "venue": venue, "symbol": "BTC-USD"})

    # Transport domain: 3 missing events (seq 2, 3, 4)
    assert state.stream_health.gaps == 3
    # Venue domain: perfectly continuous (50 -> 51 = 0 gaps!)
    assert state.health[venue].gaps == 0
    assert state.health[venue].last_feed_seq == 51


def test_venue_gap_with_continuous_transport_sequence():
    """Verify that upstream exchange feed gaps are captured while transport stream has 0 gaps."""
    state = ObserverState(subscription_filter="ALL")
    venue = "DATABENTO"

    # Transport seq 1 -> venue seq 10
    state.process_event({"seq": 1, "feed_seq": 10, "venue": venue, "symbol": "BTC-USD"})

    # Exchange dropped frames upstream; transport seq 2 arrives with venue seq 15
    state.process_event({"seq": 2, "feed_seq": 15, "venue": venue, "symbol": "BTC-USD"})

    # Transport domain: perfectly continuous (1 -> 2 = 0 gaps!)
    assert state.stream_health.gaps == 0
    # Venue domain: 4 missing exchange events (11, 12, 13, 14)
    assert state.health[venue].gaps == 4
    assert state.health[venue].last_feed_seq == 15


def test_reconnect_and_sequence_reset_behavior():
    """Verify that an exchange connection reset (seq dropping from >10 to <=1) increments feed_resets."""
    state = ObserverState(subscription_filter="ALL")
    venue = "BINANCE"

    # Run up to sequence 500
    state.process_event(
        {"seq": 1, "feed_seq": 500, "venue": venue, "symbol": "BTC-USDT"}
    )
    assert state.health[venue].last_feed_seq == 500
    assert state.health[venue].feed_resets == 0

    # Reconnect event resets venue sequence to 1
    state.process_event({"seq": 2, "feed_seq": 1, "venue": venue, "symbol": "BTC-USDT"})
    assert state.health[venue].feed_resets == 1
    assert state.health[venue].last_feed_seq == 1
    # Out of order must NOT be incremented on a clean reset
    assert state.health[venue].out_of_order == 0


def test_snapshot_sequence_semantics():
    """Verify that snapshot frames synchronize last_feed_seq without triggering false gap alerts."""
    state = ObserverState(subscription_filter="ALL")
    venue = "COINBASE"

    # Live feed at seq 50
    state.process_event({"seq": 1, "feed_seq": 50, "venue": venue, "symbol": "BTC-USD"})

    # Snapshot resync arrives with seq 200 and snapshot flag
    state.process_event(
        {
            "seq": 2,
            "feed_seq": 200,
            "venue": venue,
            "symbol": "BTC-USD",
            "is_snapshot": True,
        }
    )

    assert state.health[venue].feed_snapshots == 1
    assert state.health[venue].last_feed_seq == 200
    # Must NOT record a gap of 149
    assert state.health[venue].gaps == 0

    # Subsequent incremental update at seq 201
    state.process_event(
        {"seq": 3, "feed_seq": 201, "venue": venue, "symbol": "BTC-USD"}
    )
    assert state.health[venue].last_feed_seq == 201
    assert state.health[venue].gaps == 0


def test_channel_sequence_dispositions_and_epoch_zero():
    """Verify ChannelSequenceState.update() returns explicit SequenceDisposition and never rewinds."""
    ch = ChannelSequenceState(
        channel_key="gw1:BTC-USD", gateway_id="gw1", symbol="BTC-USD"
    )

    assert ch.update(None) == SequenceDisposition.UNAVAILABLE
    assert ch.update(10, epoch_id=0) == SequenceDisposition.ACCEPTED
    assert ch.current_epoch_id == 0
    assert ch.last_feed_seq == 10

    # Duplicate and out-of-order in epoch 0
    assert ch.update(10, epoch_id=0) == SequenceDisposition.DUPLICATE
    assert ch.update(8, epoch_id=0) == SequenceDisposition.OUT_OF_ORDER
    assert ch.last_feed_seq == 10  # Must NOT rewind

    # Epoch transition 0 -> 1
    assert ch.update(1, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch.current_epoch_id == 1
    assert ch.last_feed_seq == 1

    # Stale epoch 0 (including stale snapshot!) must be rejected without rewinding epoch or seq
    assert ch.update(50, epoch_id=0) == SequenceDisposition.STALE_EPOCH
    assert (
        ch.update(51, epoch_id=0, is_snapshot=True) == SequenceDisposition.STALE_EPOCH
    )
    assert ch.current_epoch_id == 1
    assert ch.last_feed_seq == 1
    assert ch.stale_epoch_packets == 2


def test_independent_gateway_channel_domains():
    """Verify stale-epoch or out-of-order packets on one gateway/channel do not poison another."""
    state = ObserverState()

    # Gateway A establishes BTC-USD at epoch 2, seq 50
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "gateway_id": "gw_a",
            "epoch_id": 2,
            "feed_seq": 50,
            "bid": 65000.0,
            "ask": 65002.0,
        }
    )
    # Gateway B establishes ETH-USDT at epoch 2, seq 10
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "ETHUSDT",
            "gateway_id": "gw_b",
            "epoch_id": 2,
            "feed_seq": 10,
            "bid": 3500.0,
            "ask": 3502.0,
        }
    )

    # Stale epoch 1 on gw_a BTCUSDT must not affect gw_b ETHUSDT or gw_a live quote
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "gateway_id": "gw_a",
            "epoch_id": 1,
            "feed_seq": 99,
            "bid": 1000.0,
            "ask": 1001.0,
        }
    )
    assert state.quotes["BTC-USDT"]["BINANCE"].bid_price == 65000.0
    assert state.quotes["ETH-USDT"]["BINANCE"].bid_price == 3500.0

    # gw_b continues advancing cleanly
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "ETHUSDT",
            "gateway_id": "gw_b",
            "epoch_id": 2,
            "feed_seq": 11,
            "bid": 3505.0,
            "ask": 3507.0,
        }
    )
    assert state.quotes["ETH-USDT"]["BINANCE"].bid_price == 3505.0
    assert state.health["BINANCE"].channels["gw_b:ETH-USDT"].stale_epoch_packets == 0
    assert state.health["BINANCE"].channels["gw_a:BTC-USDT"].stale_epoch_packets == 1
