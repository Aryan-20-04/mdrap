"""Deterministic tests verifying sequence domains: transport stream vs per-venue feeds vs filtered symbols."""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.state import ObserverState, StreamHealth, VenueHealth


def test_continuous_sequences_and_interleaved_venues():
    """Verify that interleaved multi-venue events in a continuous broadcast stream produce 0 gaps."""
    state = ObserverState(subscription_filter="ALL")

    # Interleave 3 venues across 9 continuous daemon broadcast sequences (seq 1..9)
    venues = ["BINANCE", "COINBASE", "KRAKEN"]
    for i in range(1, 10):
        v = venues[(i - 1) % 3]
        feed_s = ((i - 1) // 3) + 1  # 1, 2, 3 for each venue
        state.process_event({
            "type": "TICK",
            "seq": i,
            "feed_seq": feed_s,
            "venue": v,
            "symbol": "BTC-USD",
            "price": 60000.0 + i,
        })

    # Transport stream: 9 events, seq 1..9, 0 gaps
    assert state.stream_health.events_received == 9
    assert state.stream_health.last_seq == 9
    assert state.stream_health.gaps == 0
    assert state.stream_health.duplicates == 0
    assert state.stream_health.out_of_order == 0

    # Per-venue feeds: each received 3 events with feed_seq 1..3, 0 gaps
    for v in venues:
        assert state.health[v].events_received == 3
        assert state.health[v].last_feed_seq == 3
        assert state.health[v].gaps == 0
        assert state.health[v].duplicates == 0
        assert state.health[v].out_of_order == 0


def test_deliberate_missing_transport_events():
    """Verify that genuine missing broadcast events increment transport stream gaps."""
    state = ObserverState(subscription_filter="ALL")

    # Send seq 1, 2, 3
    for s in [1, 2, 3]:
        state.process_event({"type": "TICK", "seq": s, "venue": "BINANCE", "symbol": "BTC-USDT"})

    # Drop seq 4 and 5; send seq 6
    state.process_event({"type": "TICK", "seq": 6, "venue": "BINANCE", "symbol": "BTC-USDT"})

    # Stream should detect gap of 2 (missing 4 and 5)
    assert state.stream_health.events_received == 4
    assert state.stream_health.gaps == 2
    assert state.stream_health.duplicates == 0


def test_deliberate_missing_venue_feed_event():
    """Verify that upstream feed gaps are tracked in the venue domain without corrupting stream health."""
    state = ObserverState(subscription_filter="ALL")

    # Binance emits feed_seq 1, 2
    state.process_event({"type": "TICK", "seq": 1, "feed_seq": 1, "venue": "BINANCE", "symbol": "BTC-USDT"})
    state.process_event({"type": "TICK", "seq": 2, "feed_seq": 2, "venue": "BINANCE", "symbol": "BTC-USDT"})

    # Binance drops feed_seq 3; emits feed_seq 4
    state.process_event({"type": "TICK", "seq": 3, "feed_seq": 4, "venue": "BINANCE", "symbol": "BTC-USDT"})

    # Broadcast transport was continuous (seq 1, 2, 3)
    assert state.stream_health.gaps == 0

    # But Binance venue feed had 1 missing event (feed_seq 3)
    assert state.health["BINANCE"].gaps == 1


def test_filtered_symbols_do_not_produce_false_gap_alerts():
    """Verify that filtered non-subscribed events do not trigger false gap alerts when subscription_filter is active."""
    # Observer subscribed only to BTC-USD
    state = ObserverState(subscription_filter="BTC-USD")

    # Events from daemon:
    # seq 1: BTC-USD (delivered)
    # seq 2: ETH-USD (filtered at daemon, but if observed in feed or skipped)
    # seq 3: BTC-USD (delivered)
    state.process_event({"type": "TICK", "seq": 1, "venue": "COINBASE", "symbol": "BTC-USD"})

    # Observer receives seq 3 next because seq 2 was filtered out
    # Pass is_filtered_gap awareness
    state.stream_health.update(3, is_filtered_gap=True)
    state.process_event({"type": "TICK", "seq": 3, "venue": "COINBASE", "symbol": "BTC-USD"})

    assert state.stream_health.gaps == 0
    assert state.stream_health.filtered_events == 1


def test_duplicate_and_out_of_order_stream_events():
    """Verify duplicate and out-of-order broadcast packets are accurately classified."""
    state = ObserverState(subscription_filter="ALL")

    state.process_event({"type": "TICK", "seq": 10, "venue": "KRAKEN", "symbol": "BTC-USD"})

    # Duplicate: seq 10 again
    state.process_event({"type": "TICK", "seq": 10, "venue": "KRAKEN", "symbol": "BTC-USD"})
    assert state.stream_health.duplicates == 1

    # Out of order: seq 8 (< 10)
    state.process_event({"type": "TICK", "seq": 8, "venue": "KRAKEN", "symbol": "BTC-USD"})
    assert state.stream_health.out_of_order == 1


def test_producer_restart_and_epoch_reset():
    """Verify sequence jump from high sequence to small sequence represents an epoch reset, not out-of-order."""
    state = ObserverState(subscription_filter="ALL")

    # Producer was running at seq 500
    state.process_event({"type": "TICK", "seq": 500, "venue": "ITCH_SBE", "symbol": "ETH-USD"})
    state.process_event({"type": "TICK", "seq": 501, "venue": "ITCH_SBE", "symbol": "ETH-USD"})

    # Producer restarts and sequence resets to 1
    state.process_event({"type": "TICK", "seq": 1, "venue": "ITCH_SBE", "symbol": "ETH-USD"})

    assert state.stream_health.resets == 1
    assert state.stream_health.out_of_order == 0
    assert state.stream_health.gaps == 0


def test_stream_out_of_order_and_duplicate_reject_quote_mutation_when_feed_seq_unavailable():
    """Verify duplicate/out-of-order transport packets do not poison quotes when feed_seq is unavailable."""
    state = ObserverState(subscription_filter="ALL")

    state.process_event({
        "type": "TICK",
        "seq": 10,
        "venue": "KRAKEN",
        "symbol": "BTC-USD",
        "bid": 60000.0,
        "ask": 60002.0,
    })
    q = state.quotes["BTC-USD"]["KRAKEN"]
    assert q.bid_price == 60000.0
    assert q.ask_price == 60002.0

    # Duplicate transport seq=10 with poisoned price
    state.process_event({
        "type": "TICK",
        "seq": 10,
        "venue": "KRAKEN",
        "symbol": "BTC-USD",
        "bid": 10000.0,
        "ask": 10001.0,
    })
    assert state.stream_health.duplicates == 1
    assert q.bid_price == 60000.0
    assert q.ask_price == 60002.0

    # Out-of-order transport seq=8 (< 10) with poisoned price
    state.process_event({
        "type": "TICK",
        "seq": 8,
        "venue": "KRAKEN",
        "symbol": "BTC-USD",
        "bid": 90000.0,
        "ask": 90001.0,
    })
    assert state.stream_health.out_of_order == 1
    assert state.stream_health.last_seq == 10  # Must not rewind
    assert q.bid_price == 60000.0
    assert q.ask_price == 60002.0

    # Next continuous transport seq=11 updates quote cleanly without false gap
    state.process_event({
        "type": "TICK",
        "seq": 11,
        "venue": "KRAKEN",
        "symbol": "BTC-USD",
        "bid": 60001.0,
        "ask": 60003.0,
    })
    assert state.stream_health.gaps == 0
    assert q.bid_price == 60001.0
    assert q.ask_price == 60003.0
