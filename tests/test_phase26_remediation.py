"""MDRAP Phase 2.6 — Deterministic Epoch & Latency Accounting Remediation Tests.

Comprehensive unit and adversarial test suite covering:
1. Sequence Identity & Epoch Model:
   - Initial sequence zero
   - Out-of-order zero after higher sequence
   - Duplicate sequence
   - Ordinary forward gap
   - Delayed packet from previous epoch (stale epoch packet)
   - Explicit epoch transition (monotonic reset)
   - Snapshot followed by incremental updates
   - Multiple gateways with independent sequence domains
   - Gateway reconnect without venue sequence reset
   - Venue sequence reset without transport reconnect
   - Missing epoch metadata (epoch_unavailable)
   - Missing venue sequence metadata (feed_seq_unavailable)
   - Malformed epoch and sequence values
   - Legacy payload without new metadata
   - Interleaved messages from different symbols and sources

2. Inverted Timestamp & Latency Accounting:
   - Inverted ingress boundary (t0 < t_ingest)
   - Inverted engine boundary (t_bcast < t0)
   - Inverted transport boundary (t_recv < t_bcast)
   - Equal timestamps (delta == 0)
   - Missing timestamps (t <= 0)
   - Single-count invalid accounting (exactly once)
   - Exclusion of invalid observations from valid samples and percentiles
   - Preservation of telescoping identity on valid measurements
"""

import math
import os
import sys
import time
import pytest

# Ensure mdrap and observer in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "external_sidecar", "mdrap_observer")))

from mdrap.latency import MonotonicLatencyTracker, StageLatencyMetric, ReservoirSampler
from observer.state import ObserverState, VenueHealth, ChannelSequenceState


# ============================================================================
# WORKSTREAM A & C: SEQUENCE & DETERMINISTIC EPOCH TESTS
# ============================================================================

def test_seq_initial_sequence_zero():
    """Verify sequence zero is accepted as a valid initial sequence number."""
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(0, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")

    assert v.has_sequence_feed is True
    assert v.last_feed_seq == 0
    assert v.feed_gaps == 0
    assert v.feed_out_of_order == 0
    assert v.feed_duplicates == 0
    assert v.feed_resets == 0

    # Follow-up with sequence 1
    v.update_sequence(1, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")
    assert v.last_feed_seq == 1
    assert v.feed_gaps == 0
    assert v.feed_out_of_order == 0


def test_seq_out_of_order_zero_after_higher_sequence():
    """Verify out-of-order sequence zero does NOT trigger an epoch reset or false gap."""
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(50, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")
    assert v.last_feed_seq == 50

    # Delayed sequence 0 arrives within the same epoch
    v.update_sequence(0, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")
    assert v.feed_out_of_order == 1
    assert v.feed_resets == 0
    # last_feed_seq MUST NOT rewind to 0
    assert v.last_feed_seq == 50

    # Next normal packet in epoch arrives
    v.update_sequence(51, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")
    assert v.last_feed_seq == 51
    # Zero false gaps!
    assert v.feed_gaps == 0


def test_seq_duplicate_sequence():
    """Verify duplicate sequence detection within the same epoch."""
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(100, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")
    v.update_sequence(100, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")

    assert v.feed_duplicates == 1
    assert v.feed_gaps == 0
    assert v.feed_out_of_order == 0
    assert v.last_feed_seq == 100


def test_seq_ordinary_forward_gap():
    """Verify that a genuine forward sequence jump correctly increments feed_gaps."""
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(10, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")
    # Sequence jumps to 15 (missing 11, 12, 13, 14 -> gap of 4)
    v.update_sequence(15, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")

    assert v.feed_gaps == 4
    assert v.last_feed_seq == 15
    assert v.feed_duplicates == 0
    assert v.feed_out_of_order == 0


def test_seq_delayed_packet_from_previous_epoch():
    """Verify packets from older epochs are recorded as stale_epoch_packets without creating gaps."""
    v = VenueHealth(venue="BINANCE")
    # Epoch 1 reaches sequence 5000
    v.update_sequence(5000, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")

    # Transition to Epoch 2 starting at sequence 1
    v.update_sequence(1, epoch_id=2, gateway_id="gw1", symbol="BTC-USDT")
    assert v.feed_resets == 1
    assert v.feed_gaps == 0
    assert v.last_feed_seq == 1

    # Epoch 2 advances to 2 and 3
    v.update_sequence(2, epoch_id=2, gateway_id="gw1", symbol="BTC-USDT")
    v.update_sequence(3, epoch_id=2, gateway_id="gw1", symbol="BTC-USDT")
    assert v.last_feed_seq == 3

    # Delayed packet from Epoch 1 arrives with sequence 4999!
    v.update_sequence(4999, epoch_id=1, gateway_id="gw1", symbol="BTC-USDT")

    # MUST be discarded as stale epoch packet without incrementing forward gap!
    assert v.stale_epoch_packets == 1
    assert v.feed_gaps == 0
    assert v.last_feed_seq == 3

    # Next packet in Epoch 2 continues normally
    v.update_sequence(4, epoch_id=2, gateway_id="gw1", symbol="BTC-USDT")
    assert v.last_feed_seq == 4
    assert v.feed_gaps == 0


def test_seq_explicit_epoch_transition():
    """Verify explicit epoch transitions reset baseline cleanly with 0 gaps."""
    v = VenueHealth(venue="KRAKEN")
    v.update_sequence(1000, epoch_id="2026-11-21-session1", gateway_id="gw1", symbol="ETH-USD")
    v.update_sequence(1, epoch_id="2026-11-21-session2", gateway_id="gw1", symbol="ETH-USD")

    assert v.feed_resets == 1
    assert v.feed_gaps == 0
    assert v.last_feed_seq == 1


def test_seq_snapshot_followed_by_incremental_updates():
    """Verify snapshot frames re-anchor sequence baseline cleanly."""
    v = VenueHealth(venue="COINBASE")
    v.update_sequence(50, epoch_id=1, gateway_id="gw1", symbol="BTC-USD")

    # Snapshot resync arrives with sequence 200
    v.update_sequence(200, is_snapshot=True, epoch_id=1, gateway_id="gw1", symbol="BTC-USD")
    assert v.feed_snapshots == 1
    assert v.last_feed_seq == 200
    assert v.feed_gaps == 0

    # Incremental update 201 follows
    v.update_sequence(201, epoch_id=1, gateway_id="gw1", symbol="BTC-USD")
    assert v.last_feed_seq == 201
    assert v.feed_gaps == 0


def test_seq_multiple_gateways_independent_sequence_domains():
    """Verify multiple gateways for the same venue maintain independent sequence tracking."""
    v = VenueHealth(venue="BINANCE")

    # Gateway A stream
    v.update_sequence(1000, gateway_id="gw-primary", symbol="BTC-USDT")
    # Gateway B stream with completely different sequence range
    v.update_sequence(100, gateway_id="gw-secondary", symbol="BTC-USDT")

    # Interleaved packet from Gateway A
    v.update_sequence(1001, gateway_id="gw-primary", symbol="BTC-USDT")
    # Interleaved packet from Gateway B
    v.update_sequence(101, gateway_id="gw-secondary", symbol="BTC-USDT")

    # Neither channel should report a gap or out-of-order!
    assert v.feed_gaps == 0
    assert v.feed_out_of_order == 0
    assert v.feed_duplicates == 0
    assert len(v.channels) == 2


def test_seq_gateway_reconnect_without_venue_sequence_reset():
    """Verify gateway reconnect where exchange sequence continues monotonically."""
    state = ObserverState(subscription_filter="ALL")
    venue = "BINANCE"

    # Gateway drops transport packets (seq jumps 10 -> 30)
    # Venue feed continues monotonically (100 -> 101)
    state.process_event({"seq": 10, "feed_seq": 100, "venue": venue, "symbol": "BTC-USDT"})
    state.process_event({"seq": 30, "feed_seq": 101, "venue": venue, "symbol": "BTC-USDT"})

    assert state.stream_health.gaps == 19
    assert state.health[venue].feed_gaps == 0
    assert state.health[venue].feed_resets == 0


def test_seq_venue_sequence_reset_without_transport_reconnect():
    """Verify venue sequence reset while transport stream sequence remains continuous."""
    state = ObserverState(subscription_filter="ALL")
    venue = "BINANCE"

    # Transport sequence: 1 -> 2
    # Venue sequence: 5000 (epoch 1) -> 1 (epoch 2)
    state.process_event({"seq": 1, "feed_seq": 5000, "epoch_id": 1, "venue": venue, "symbol": "BTC-USDT"})
    state.process_event({"seq": 2, "feed_seq": 1, "epoch_id": 2, "venue": venue, "symbol": "BTC-USDT"})

    assert state.stream_health.gaps == 0
    assert state.health[venue].feed_resets == 1
    assert state.health[venue].feed_gaps == 0


def test_seq_missing_epoch_metadata():
    """Verify that absent epoch metadata increments epoch_unavailable diagnostic."""
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(10, epoch_id=None, gateway_id="gw1", symbol="BTC-USDT")
    v.update_sequence(11, epoch_id=None, gateway_id="gw1", symbol="BTC-USDT")

    assert v.epoch_unavailable == 2
    assert v.feed_gaps == 0


def test_seq_missing_venue_sequence_metadata():
    """Verify that absent venue sequence increments feed_seq_unavailable diagnostic."""
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(None)

    assert v.feed_seq_unavailable == 1
    assert v.feed_gaps == 0


def test_seq_malformed_epoch_and_sequence_values():
    """Verify that malformed sequences or epochs do not crash state machine."""
    v = VenueHealth(venue="BINANCE")
    # Non-int string passed as sequence handled safely
    v.update_sequence(None)
    # Int sequence with string epoch
    v.update_sequence(10, epoch_id="session_alpha")
    assert v.last_feed_seq == 10

    v.update_sequence(11, epoch_id="session_alpha")
    assert v.last_feed_seq == 11
    assert v.feed_gaps == 0


def test_seq_legacy_payload_without_new_metadata():
    """Verify legacy payload lacking epoch_id, gateway_id, and is_snapshot processes safely."""
    state = ObserverState(subscription_filter="ALL")
    legacy_event = {
        "type": "TICK",
        "seq": 1,
        "feed_seq": 100,
        "venue": "BINANCE",
        "symbol": "BTC-USDT",
        "price": 65000.0,
    }
    state.process_event(legacy_event)

    health = state.health["BINANCE"]
    assert health.events_received == 1
    assert health.last_feed_seq == 100
    assert health.epoch_unavailable == 1
    assert health.feed_gaps == 0


def test_seq_interleaved_messages_from_different_symbols():
    """Verify messages from different symbols on the same venue do not cross-contaminate."""
    v = VenueHealth(venue="BINANCE")

    v.update_sequence(10, gateway_id="gw1", symbol="BTC-USDT")
    v.update_sequence(500, gateway_id="gw1", symbol="ETH-USDT")

    v.update_sequence(11, gateway_id="gw1", symbol="BTC-USDT")
    v.update_sequence(501, gateway_id="gw1", symbol="ETH-USDT")

    assert v.feed_gaps == 0
    assert v.feed_out_of_order == 0


# ============================================================================
# WORKSTREAM B & C: LATENCY INVALID ACCOUNTING TESTS
# ============================================================================

def test_lat_inverted_ingress_boundary_increments_invalid_count():
    """Verify that inverted ingress boundary (t0 < t_ingest) increments invalid_count exactly once."""
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 2_000_000,
        "t0_ns": 1_000_000,  # INVERTED: t0 < ingest
        "broadcast_ts_ns": 3_000_000,
        "engine_ns": 2_000_000,
    }
    res = tracker.record_event_latencies(event, t_recv_ns=4_000_000)

    # Ingress was inverted: result is None, invalid_count incremented exactly once
    assert res["ingress_us"] is None
    assert tracker.ingress.invalid_count == 1
    assert tracker.ingress.count == 0

    # Other valid stages were recorded normally
    assert res["engine_us"] == 2000.0
    assert tracker.engine.count == 1
    assert tracker.engine.invalid_count == 0


def test_lat_inverted_engine_boundary_increments_invalid_count():
    """Verify that inverted engine boundary increments invalid_count exactly once."""
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 1_000_000,
        "t0_ns": 2_000_000,
        "broadcast_ts_ns": 1_500_000,  # INVERTED: bcast < t0
        "engine_ns": None,
    }
    res = tracker.record_event_latencies(event, t_recv_ns=4_000_000)

    assert res["engine_us"] is None
    assert tracker.engine.invalid_count == 1
    assert tracker.engine.count == 0


def test_lat_inverted_transport_boundary_increments_invalid_count():
    """Verify that inverted transport boundary (now < bcast) increments invalid_count exactly once."""
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 1_000_000,
        "t0_ns": 2_000_000,
        "broadcast_ts_ns": 5_000_000,
        "engine_ns": 3_000_000,
    }
    # t_recv_ns is 4,000,000 (< 5,000,000 broadcast)
    res = tracker.record_event_latencies(event, t_recv_ns=4_000_000)

    assert res["transport_us"] is None
    assert tracker.transport.invalid_count == 1
    assert tracker.transport.count == 0


def test_lat_equal_timestamps_recorded_as_valid_zero():
    """Verify that equal timestamps (delta == 0) are recorded as valid 0 us."""
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 1_000_000,
        "t0_ns": 1_000_000,  # delta = 0
        "broadcast_ts_ns": 1_000_000,
        "engine_ns": 0,
    }
    res = tracker.record_event_latencies(event, t_recv_ns=1_000_000)

    assert res["ingress_us"] == 0.0
    assert res["engine_us"] == 0.0
    assert res["transport_us"] == 0.0
    assert res["e2e_us"] == 0.0

    assert tracker.ingress.count == 1
    assert tracker.ingress.invalid_count == 0
    assert tracker.ingress.summary()["min_us"] == 0.0


def test_lat_missing_timestamps_do_not_increment_invalid_count():
    """Verify that missing timestamps (0 or None) leave result as None without incrementing invalid_count."""
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 0,  # Missing
        "t0_ns": 0,          # Missing
        "broadcast_ts_ns": 0,# Missing
        "engine_ns": None,
    }
    res = tracker.record_event_latencies(event, t_recv_ns=1_000_000)

    assert res["ingress_us"] is None
    assert res["engine_us"] is None
    assert res["transport_us"] is None
    assert res["e2e_us"] is None

    # Uninstrumented timestamps must NOT be counted as invalid anomalies
    assert tracker.ingress.invalid_count == 0
    assert tracker.engine.invalid_count == 0
    assert tracker.transport.invalid_count == 0
    assert tracker.e2e.invalid_count == 0


def test_lat_invalid_observations_excluded_from_samples_and_percentiles():
    """Verify that invalid observations never pollute reservoir samples or percentiles."""
    metric = StageLatencyMetric("test_stage", reservoir_capacity=100)

    # Record 10 valid durations (10 us each)
    for _ in range(10):
        metric.record(10_000)

    # Attempt to record 5 invalid durations
    metric.record(-500)
    metric.record(-1)
    metric.record(float("nan"))
    metric.record(float("inf"))
    metric.record(float("-inf"))

    assert metric.count == 10
    assert metric.invalid_count == 5

    summ = metric.summary()
    assert summ["count"] == 10
    assert summ["invalid_count"] == 5
    assert summ["p50_us"] == 10.0
    assert summ["p99_us"] == 10.0
    assert summ["all_event_max_us"] == 10.0


def test_lat_valid_measurements_satisfy_telescoping_identity():
    """Verify that valid measurements satisfy the telescoping additive identity:
    ingress + engine + transport == e2e
    """
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 1_000_000_000,
        "t0_ns": 1_000_020_000,         # ingress = 20 us
        "broadcast_ts_ns": 1_000_050_000,# engine = 30 us
        "engine_ns": 30_000,
    }
    res = tracker.record_event_latencies(event, t_recv_ns=1_000_090_000)  # transport = 40 us

    assert res["ingress_us"] == 20.0
    assert res["engine_us"] == 30.0
    assert res["transport_us"] == 40.0
    assert res["e2e_us"] == 90.0

    # Telescoping identity check
    sum_components = round(res["ingress_us"] + res["engine_us"] + res["transport_us"], 2)
    assert sum_components == res["e2e_us"]
