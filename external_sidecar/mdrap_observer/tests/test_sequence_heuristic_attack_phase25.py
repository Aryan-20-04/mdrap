"""MDRAP Phase 2.5 — Reconnect Heuristic & Sequence State Machine Attack Tests.

Adversarial exploration of the sequence state machine and heuristic:
    `seq <= 1 and last_feed_seq > 10`

Tests the 10 required operational scenarios to establish empirical boundaries
of what the current heuristic can and cannot distinguish.
"""

import os
import sys
import pytest

# Ensure mdrap and observer in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.state import VenueHealth, StreamHealth, ObserverState


def test_scenario_1_legitimate_reset_from_5000_to_1():
    """Scenario 1: Legitimate reset from 5000 to 1.
    Expected: feed_resets incremented, 0 gaps, 0 out-of-order.
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(5000)
    v.update_sequence(1)

    assert v.feed_resets == 1
    assert v.feed_gaps == 0
    assert v.feed_out_of_order == 0
    assert v.last_feed_seq == 1


def test_scenario_2_duplicate_sequence_1():
    """Scenario 2: Duplicate sequence 1.
    Expected: Correctly identified as duplicate, not as reset.
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(1)
    v.update_sequence(1)

    assert v.feed_duplicates == 1
    assert v.feed_resets == 0
    assert v.feed_gaps == 0


def test_scenario_3_out_of_order_sequence_0_remediated():
    """Scenario 3: Out-of-order sequence 0 arriving late (e.g. last_feed_seq=50, arrives seq=0).
    Phase 2.6 Remediation: Sequence 0 is recognized as out-of-order packet, NOT as an epoch reset.
    last_feed_seq does not rewind to 0, preventing subsequent false gaps.
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(50)
    # A delayed sequence 0 arrives
    v.update_sequence(0)

    # Remediated: Treated as out_of_order, NOT a reset
    assert v.feed_resets == 0
    assert v.feed_out_of_order == 1
    assert v.last_feed_seq == 50

    # Next normal packet arrives
    v.update_sequence(51)
    # Remediated: Continuous with 50, zero false gaps!
    assert v.feed_gaps == 0
    assert v.last_feed_seq == 51


def test_scenario_4_snapshot_beginning_at_sequence_1():
    """Scenario 4: Snapshot beginning at sequence 1.
    Explicit is_snapshot=True overrides heuristic cleanly.
    Without is_snapshot=True, falls back to heuristic reset.
    """
    # Case 4A: With explicit snapshot flag
    v1 = VenueHealth(venue="BINANCE")
    v1.update_sequence(5000)
    v1.update_sequence(1, is_snapshot=True)
    assert v1.feed_snapshots == 1
    assert v1.feed_resets == 0
    assert v1.feed_gaps == 0

    # Case 4B: Without explicit snapshot flag (unflagged snapshot)
    v2 = VenueHealth(venue="BINANCE")
    v2.update_sequence(5000)
    v2.update_sequence(1, is_snapshot=False)
    # Unflagged snapshot is categorized as heuristic reset
    assert v2.feed_snapshots == 0
    assert v2.feed_resets == 1


def test_scenario_5_reconnect_without_sequence_reset():
    """Scenario 5: Reconnect occurs, but exchange sequence continues monotonically.
    During disconnect, exchange produced seq 5001-5020.
    Client reconnects and receives seq 5021.
    Expected: Detected as gap=20. State machine cannot know network disconnected.
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(5000)
    # Reconnect happens behind the scenes; next received is 5021
    v.update_sequence(5021)
    assert v.feed_gaps == 20
    assert v.feed_resets == 0


def test_scenario_6_sequence_reset_without_reconnect():
    """Scenario 6: Sequence reset occurs (e.g. daily exchange midnight rollover)
    while TCP connection remains continuously open and connected.
    Expected: Captured by heuristic as feed_resets += 1. Cannot distinguish from disconnect.
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(120000)
    # Midnight UTC rollover
    v.update_sequence(1)
    assert v.feed_resets == 1
    assert v.feed_gaps == 0


def test_scenario_7_delayed_packet_from_previous_connection_epoch():
    """Scenario 7: Delayed packet from previous epoch arrives after a reset has advanced.
    Reset occurred to 1, then 2, 3 arrived. Then delayed packet 4999 from old epoch arrives.
    HEURISTIC LIMITATION: 4999 > 3 + 1 -> Treated as a forward gap of 4995!
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(5000)
    v.update_sequence(1)  # Reset
    v.update_sequence(2)
    v.update_sequence(3)

    # Delayed old-epoch packet 4999 arrives!
    v.update_sequence(4999)
    # Heuristic failure: massive false gap
    assert v.feed_gaps == (4999 - 3 - 1)  # 4995 false gap
    assert v.last_feed_seq == 4999


def test_scenario_8_interleaved_events_from_two_connections():
    """Scenario 8: Interleaved events from two redundant gateways/connections.
    Gateway A is at 1000, Gateway B is at 100.
    HEURISTIC LIMITATION: State machine alternates between out_of_order and massive gaps.
    """
    v = VenueHealth(venue="BINANCE")
    v.update_sequence(1000)  # From Gateway A
    v.update_sequence(100)   # From Gateway B (100 < 1000, not <= 1) -> out_of_order
    assert v.feed_out_of_order == 1
    assert v.last_feed_seq == 100

    v.update_sequence(1001)  # From Gateway A (1001 > 100 + 1) -> false gap of 900!
    assert v.feed_gaps == 900


def test_scenario_9_venue_sequence_reset_with_continuous_transport():
    """Scenario 9: Venue sequence resets while transport stream sequence remains continuous.
    Expected: Transport has 0 gaps, Venue registers reset cleanly.
    """
    state = ObserverState(subscription_filter="ALL")
    # Event 1: Transport seq 1, Venue seq 5000
    state.process_event({"seq": 1, "feed_seq": 5000, "source": "BINANCE", "sym": "BTC-USDT", "type": "TICK", "price": 60000.0})
    # Event 2: Transport seq 2, Venue seq 1 (venue reset)
    state.process_event({"seq": 2, "feed_seq": 1, "source": "BINANCE", "sym": "BTC-USDT", "type": "TICK", "price": 60001.0})

    assert state.stream_health.gaps == 0
    assert state.health["BINANCE"].feed_resets == 1
    assert state.health["BINANCE"].feed_gaps == 0


def test_scenario_10_transport_reconnection_without_venue_reset():
    """Scenario 10: Transport disconnects (seq jumps from 10 to 50),
    while upstream venue sequence continues continuously (1000, 1001).
    Expected: Transport gaps == 39, Venue gaps == 0.
    """
    state = ObserverState(subscription_filter="ALL")
    state.process_event({"seq": 10, "feed_seq": 1000, "source": "BINANCE", "sym": "BTC-USDT", "type": "TICK", "price": 60000.0})
    state.process_event({"seq": 50, "feed_seq": 1001, "source": "BINANCE", "sym": "BTC-USDT", "type": "TICK", "price": 60001.0})

    # Transport gap: 50 - 10 - 1 = 39
    assert state.stream_health.gaps == 39
    # Venue feed continuous: 0 gaps
    assert state.health["BINANCE"].feed_gaps == 0
    assert state.health["BINANCE"].feed_resets == 0
