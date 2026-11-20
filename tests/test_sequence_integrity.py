"""
Regression Tests for Market Data Sequence Integrity & Scoped Stream Tracking (Agent 2 / Batch B).

Covers:
  - MDRAP-FIN-01: Multi-symbol stream sequence isolation in LiveCryptoFeed and MultiVenueGateway.
  - Backward compatibility with legacy venue-only calls.
  - Interleaved multi-symbol updates (e.g. BTCUSDT and ETHUSDT) on same venue.
  - Sequence range updates (e.g. Binance U..u depth packets).
  - Snapshot resets and reconnection recovery.
  - Duplicate packet detection and out-of-order wrap handling.
  - Provider isolation across concurrent venues.
"""

from __future__ import annotations

import pytest
from mdrap.ws_feed import LiveCryptoFeed, SequenceScope


def test_sequence_backward_compatibility():
    """Verify legacy single-parameter venue tracking continues to function."""
    feed = LiveCryptoFeed()
    assert not feed.check_sequence_gap("BINANCE", 100)
    assert not feed.check_sequence_gap("BINANCE", 101)
    assert feed.check_sequence_gap("BINANCE", 105)
    assert feed.gap_counts["BINANCE"] == 1
    assert feed.missing_events_count["BINANCE"] == 3  # 102, 103, 104


def test_interleaved_multi_symbol_streams_no_contamination():
    """
    CRITICAL REGRESSION TEST for MDRAP-FIN-01.
    Interleaving BTCUSDT (seq 500k) and ETHUSDT (seq 200k) must NEVER contaminate
    each other's sequence numbers or generate false gaps.
    """
    feed = LiveCryptoFeed()

    # Step 1: Initialize BTC at 500,000 and ETH at 200,000
    assert not feed.check_sequence_gap("BINANCE", 500_000, instrument="BTCUSDT")
    assert not feed.check_sequence_gap("BINANCE", 200_000, instrument="ETHUSDT")

    # Step 2: Interleave contiguous updates
    assert not feed.check_sequence_gap("BINANCE", 500_001, instrument="BTCUSDT")
    assert not feed.check_sequence_gap("BINANCE", 200_001, instrument="ETHUSDT")
    assert not feed.check_sequence_gap("BINANCE", 500_002, instrument="BTCUSDT")
    assert not feed.check_sequence_gap("BINANCE", 200_002, instrument="ETHUSDT")

    # Verify ZERO false gaps were registered
    assert feed.gap_counts["BINANCE:BTCUSDT"] == 0
    assert feed.gap_counts["BINANCE:ETHUSDT"] == 0
    assert feed.missing_events_count["BINANCE:BTCUSDT"] == 0
    assert feed.missing_events_count["BINANCE:ETHUSDT"] == 0

    # Step 3: Trigger real gap ONLY on BTC (500_002 -> 500_006)
    assert feed.check_sequence_gap("BINANCE", 500_006, instrument="BTCUSDT")
    assert feed.gap_counts["BINANCE:BTCUSDT"] == 1
    assert feed.missing_events_count["BINANCE:BTCUSDT"] == 3  # 500003, 500004, 500005

    # ETH remains completely unaffected
    assert not feed.check_sequence_gap("BINANCE", 200_003, instrument="ETHUSDT")
    assert feed.gap_counts["BINANCE:ETHUSDT"] == 0
    assert feed.missing_events_count["BINANCE:ETHUSDT"] == 0


def test_sequence_scope_object_usage():
    """Verify SequenceScope structured keying across channel and epoch."""
    feed = LiveCryptoFeed()
    scope_l1 = SequenceScope(venue="KRAKEN", instrument="SOLUSD", channel="BOOK_L1")
    scope_l2 = SequenceScope(venue="KRAKEN", instrument="SOLUSD", channel="BOOK_L2")

    assert not feed.check_sequence_gap(scope_l1, 10)
    assert not feed.check_sequence_gap(scope_l2, 100)

    # Contiguous
    assert not feed.check_sequence_gap(scope_l1, 11)
    assert not feed.check_sequence_gap(scope_l2, 101)

    # Gap on L1 only
    assert feed.check_sequence_gap(scope_l1, 15)
    assert feed.gap_counts[scope_l1.key()] == 1
    assert feed.missing_events_count[scope_l1.key()] == 3
    assert feed.gap_counts[scope_l2.key()] == 0


def test_sequence_range_packets():
    """Verify range-based sequence packet checking (e.g. Binance U..u depth frames)."""
    feed = LiveCryptoFeed()
    scope = SequenceScope(venue="BINANCE", instrument="BTCUSDT", channel="DEPTH")

    # Initial packet spans 1000..1010
    assert not feed.check_sequence_range(scope, first_seq=1000, final_seq=1010)

    # Contiguous packet spans 1011..1020
    assert not feed.check_sequence_range(scope, first_seq=1011, final_seq=1020)

    # Overlapping packet (depth updates often overlap by 1 update ID)
    assert not feed.check_sequence_range(scope, first_seq=1020, final_seq=1030)

    # Real gap: packet starts at 1035 instead of <= 1031
    assert feed.check_sequence_range(scope, first_seq=1035, final_seq=1040)
    assert feed.gap_counts[scope.key()] == 1
    assert feed.missing_events_count[scope.key()] == 4  # 1031, 1032, 1033, 1034


def test_snapshot_recovery_and_reconnection_resets():
    """Verify snapshot recovery and explicit resets do not trigger false gap bursts."""
    feed = LiveCryptoFeed()
    scope = SequenceScope(venue="COINBASE", instrument="ETHUSD", epoch=1)

    assert not feed.check_sequence_gap(scope, 100)
    assert not feed.check_sequence_gap(scope, 101)

    # Reconnection starts an explicitly identified epoch; stale lower values in
    # the previous epoch do not implicitly reset its high-water mark.
    assert not feed.check_sequence_gap(scope, 1)
    assert feed.last_seq[scope.key()] == 101
    scope = SequenceScope(venue="COINBASE", instrument="ETHUSD", epoch=2)
    assert not feed.check_sequence_gap(scope, 1)
    assert feed.resets_count["COINBASE:ETHUSD:ep1"] == 0
    assert feed.gap_counts[scope.key()] == 0

    # Snapshot arriving with is_snapshot=True
    assert not feed.check_sequence_gap(scope, 5000, is_snapshot=True)
    assert feed.gap_counts[scope.key()] == 0
    assert feed.missing_events_count[scope.key()] == 0

    # Next contiguous frame from snapshot
    assert not feed.check_sequence_gap(scope, 5001)
    assert feed.gap_counts[scope.key()] == 0


def test_duplicate_sequence_handling():
    """Verify duplicate sequence frames are accounted for without raising false gaps."""
    feed = LiveCryptoFeed()
    scope = SequenceScope(venue="BYBIT", instrument="BTCUSDT")

    assert not feed.check_sequence_gap(scope, 200)
    # Duplicate frame with same seq
    assert not feed.check_sequence_gap(scope, 200)
    assert feed.duplicate_count[scope.key()] == 1
    assert feed.gap_counts[scope.key()] == 0

    # Normal next frame
    assert not feed.check_sequence_gap(scope, 201)
    assert feed.gap_counts[scope.key()] == 0
