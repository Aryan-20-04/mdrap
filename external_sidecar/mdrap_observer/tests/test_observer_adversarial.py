"""Adversarial and boundary fault injection tests for MDRAP Observer."""

import math
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.state import (
    ChannelSequenceState,
    ObserverState,
    SequenceDisposition,
    VenueHealth,
    VenueQuote,
)


def test_crossed_book_detection():
    """Verify inverted books (bid > ask) and locked books (bid == ask) are flagged."""
    # Normal book
    q_normal = VenueQuote(
        venue="BINANCE", symbol="BTC-USDT", bid_price=60000.0, ask_price=60001.0
    )
    assert not q_normal.is_crossed
    assert q_normal.spread == 1.0

    # Locked book (bid == ask)
    q_locked = VenueQuote(
        venue="BINANCE", symbol="BTC-USDT", bid_price=60000.0, ask_price=60000.0
    )
    assert q_locked.is_crossed
    assert q_locked.spread == 0.0

    # Inverted / crossed book (bid > ask)
    q_crossed = VenueQuote(
        venue="BINANCE", symbol="BTC-USDT", bid_price=60005.0, ask_price=60000.0
    )
    assert q_crossed.is_crossed
    assert q_crossed.spread == -5.0


def test_micro_price_zero_division_guard():
    """Verify zero total queue size or missing bids/asks safely falls back to mid-price."""
    # Zero sizes on both sides
    q_zero_size = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=60000.0,
        bid_size=0.0,
        ask_price=60002.0,
        ask_size=0.0,
    )
    # Must not raise ZeroDivisionError; should return mid-price 60001.0
    assert q_zero_size.micro_price == 60001.0

    # One-sided quote (only bid, no ask)
    q_one_sided = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=60000.0,
        bid_size=1.0,
        ask_price=0.0,
        ask_size=0.0,
    )
    assert q_one_sided.micro_price == 60000.0
    assert q_one_sided.spread == 0.0


def test_corrupted_and_missing_payload_fields():
    """Verify state processor does not crash on malformed, missing, or type-coerced event fields."""
    state = ObserverState()

    # Empty dictionary
    state.process_event({})

    # Missing venue/source
    state.process_event({"symbol": "BTC-USD", "price": 60000.0})

    # Unrecognized venue
    state.process_event(
        {"venue": "UNKNOWN_EXCHANGE", "symbol": "BTC-USD", "price": 60000.0}
    )

    # Non-numeric string prices and sequences
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "bid": "not_a_float",
            "ask": None,
            "sequence": "invalid_int",
        }
    )

    # Negative price values
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "BTC-USD",
            "bid": -100.0,
            "ask": -50.0,
            "sequence": 1,
        }
    )

    # Health must still track the event received from valid venue
    assert state.health["BINANCE"].events_received >= 1
    assert state.health["KRAKEN"].events_received >= 1
    assert state.health["BINANCE"].invalid_numeric_events >= 1
    assert state.health["KRAKEN"].invalid_numeric_events >= 1
    assert state.quotes["BTC-USDT"]["BINANCE"].bid_price == 0.0
    assert state.quotes["BTC-USD"]["KRAKEN"].bid_price == 0.0
    assert state.quotes["BTC-USD"]["KRAKEN"].ask_price == 0.0


def test_micro_price_negative_and_non_finite_guards():
    """Verify VenueQuote.micro_price falls back to mid_price on negative or non-finite sizes/prices."""
    # Negative bid_size would skew microprice outside [bid, ask] without guard
    q_neg_bid_sz = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=-5.0,
        ask_price=102.0,
        ask_size=10.0,
    )
    assert q_neg_bid_sz.micro_price == 101.0

    # Negative ask_size
    q_neg_ask_sz = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=10.0,
        ask_price=102.0,
        ask_size=-5.0,
    )
    assert q_neg_ask_sz.micro_price == 101.0

    # Both negative
    q_both_neg = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=-2.0,
        ask_price=102.0,
        ask_size=-3.0,
    )
    assert q_both_neg.micro_price == 101.0

    # Non-finite sizes (NaN, +inf, -inf)
    for bad_sz in (float("nan"), float("inf"), float("-inf")):
        q_nan = VenueQuote(
            venue="COINBASE",
            symbol="BTC-USD",
            bid_price=100.0,
            bid_size=bad_sz,
            ask_price=102.0,
            ask_size=4.0,
        )
        assert q_nan.micro_price == 101.0

    # One-sided zero size with valid counterpart
    q_zero_bid = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=0.0,
        ask_price=102.0,
        ask_size=4.0,
    )
    assert q_zero_bid.micro_price == 100.0

    q_zero_ask = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=4.0,
        ask_price=102.0,
        ask_size=0.0,
    )
    assert q_zero_ask.micro_price == 102.0

    # Very large finite values (1e12)
    q_large = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=1e12,
        bid_size=1e12,
        ask_price=1e12 + 2.0,
        ask_size=1e12,
    )
    assert pytest.approx(q_large.micro_price, rel=1e-9) == 1e12 + 1.0


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"price": float("nan"), "event_type": "TRADE"},
        {"price": float("inf"), "event_type": "TRADE"},
        {"price": float("-inf"), "event_type": "TRADE"},
        {"price": -1.0, "event_type": "TRADE"},
        {"price": 60005.0, "size": float("nan"), "event_type": "TRADE"},
        {"price": 60005.0, "size": float("inf"), "event_type": "TRADE"},
        {"price": 60005.0, "size": -0.1, "event_type": "TRADE"},
        {"bid": float("nan"), "ask": 60010.0},
        {"bid": float("inf"), "ask": 60010.0},
        {"bid": float("-inf"), "ask": 60010.0},
        {"bid": -10.0, "ask": 60010.0},
        {"bid": 60005.0, "ask": float("nan")},
        {"bid": 60005.0, "ask": float("inf")},
        {"bid": 60005.0, "ask": -1.0},
        {"bid": 60005.0, "bid_size": float("nan"), "ask": 60010.0},
        {"bid": 60005.0, "bid_size": float("inf"), "ask": 60010.0},
        {"bid": 60005.0, "bid_size": -1.0, "ask": 60010.0},
        {"bid": 60005.0, "ask": 60010.0, "ask_size": float("-inf")},
        {"bid": 60005.0, "ask": 60010.0, "ask_size": -0.01},
        {"bid": "not_a_float", "ask": 60010.0},
        {"bid": True, "ask": 60010.0},
    ],
)
def test_atomic_rejection_of_invalid_numeric_inputs(bad_payload):
    """Verify NaN, +/-inf, negative, boolean, and unparseable market numerics never mutate quotes or trades."""
    state = ObserverState()
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "bid": 60000.0,
            "bid_size": 2.0,
            "ask": 60002.0,
            "ask_size": 3.0,
            "feed_seq": 1,
            "timestamp": 1000.0,
        }
    )

    evt = {
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "feed_seq": 2,
        "timestamp": 2000.0,
        **bad_payload,
    }
    state.process_event(evt)

    q = state.quotes["BTC-USD"]["COINBASE"]
    assert q.bid_price == 60000.0
    assert q.bid_size == 2.0
    assert q.ask_price == 60002.0
    assert q.ask_size == 3.0
    assert q.last_update_ts == 1000.0
    assert len(state.trades["BTC-USD"]) == 0
    assert state.health["COINBASE"].invalid_numeric_events == 1
    assert state.health["COINBASE"].events_received == 2


def test_partial_updates_and_large_finite_values():
    """Verify partial None-side updates preserve counterpart and large finite values (1e12) succeed."""
    state = ObserverState()
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "bid": 60000.0,
            "bid_size": 2.0,
            "ask": 60002.0,
            "ask_size": 3.0,
            "feed_seq": 1,
        }
    )

    # Partial update: bid=None (absent), ask updated
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "bid": None,
            "ask": 60005.0,
            "ask_size": 4.0,
            "feed_seq": 2,
        }
    )
    q = state.quotes["BTC-USDT"]["BINANCE"]
    assert q.bid_price == 60000.0
    assert q.bid_size == 2.0
    assert q.ask_price == 60005.0
    assert q.ask_size == 4.0

    # Very large finite values accepted
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "bid": 1e12,
            "bid_size": 1e12,
            "ask": 1e12 + 2.0,
            "ask_size": 1e12,
            "feed_seq": 3,
        }
    )
    assert q.bid_price == 1e12
    assert q.ask_price == 1e12 + 2.0
    assert state.health["BINANCE"].invalid_numeric_events == 0


def test_stale_epoch_and_snapshot_with_feed_seq_none_rejected():
    """Workstream B: Stale-epoch packets and snapshots with feed_seq=None cannot bypass epoch validation."""
    state = ObserverState()
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 100,
            "bid": 60000.0,
            "bid_size": 2.0,
            "ask": 60005.0,
            "ask_size": 3.0,
        }
    )
    q = state.quotes["BTC-USD"]["COINBASE"]
    nbbo_before = state.get_consolidated_nbbo("BTC-USD")

    # Stale epoch 1 with feed_seq=None
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 1,
            "bid": 1000.0,
            "ask": 1001.0,
        }
    )
    # Stale epoch 1 snapshot with feed_seq=None
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 1,
            "is_snapshot": True,
            "bid": 2000.0,
            "ask": 2001.0,
        }
    )

    assert state.health["COINBASE"].stale_epoch_packets == 2
    assert q.bid_price == 60000.0
    assert q.ask_price == 60005.0
    assert state.get_consolidated_nbbo("BTC-USD") == nbbo_before


def test_invalid_numeric_and_rejected_packets_cannot_poison_epoch_or_revive_stale():
    """Workstream B: Invalid numerics (incl. bad timestamp) and rejected packets preserve STALE status and epoch."""
    state = ObserverState(stale_threshold_s=0.05)
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 10,
            "bid": 60000.0,
            "bid_size": 2.0,
            "ask": 60004.0,
            "ask_size": 2.0,
        }
    )
    time.sleep(0.08)
    state.check_all_stale()
    assert state.health["COINBASE"].status == "STALE"
    stale_ts = state.health["COINBASE"].last_seen_ts

    # 1. Invalid market numeric carrying newer epoch_id=99 -> must NOT advance epoch or revive STALE
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 99,
            "feed_seq": 1,
            "bid": float("nan"),
            "ask": 60005.0,
        }
    )
    assert state.health["COINBASE"].status == "STALE"
    assert state.health["COINBASE"].last_seen_ts == stale_ts
    assert state.health["COINBASE"].invalid_numeric_events == 1
    assert state.health["COINBASE"].channels["default:BTC-USD"].current_epoch_id == 2
    assert state.health["COINBASE"].last_feed_seq == 10

    # 2. Invalid timestamp carrying valid bid -> must reject atomically
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 11,
            "bid": 61000.0,
            "ask": 61002.0,
            "timestamp": float("nan"),
        }
    )
    assert state.health["COINBASE"].invalid_numeric_events == 2
    assert state.quotes["BTC-USD"]["COINBASE"].bid_price == 60000.0
    assert state.health["COINBASE"].status == "STALE"

    # 3. Rejected STALE_EPOCH and OUT_OF_ORDER packets must keep STALE status and last_seen_ts
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 1,
            "feed_seq": 5,
            "bid": 50000.0,
            "ask": 50001.0,
        }
    )
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 9,
            "bid": 50000.0,
            "ask": 50001.0,
        }
    )
    assert state.health["COINBASE"].status == "STALE"
    assert state.health["COINBASE"].last_seen_ts == stale_ts
    assert state.quotes["BTC-USD"]["COINBASE"].bid_price == 60000.0

    # 4. Subsequent valid epoch 2 event succeeds cleanly
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 11,
            "bid": 60010.0,
            "ask": 60012.0,
        }
    )
    assert state.health["COINBASE"].status == "CONNECTED"
    assert state.quotes["BTC-USD"]["COINBASE"].bid_price == 60010.0
    assert state.quotes["BTC-USD"]["COINBASE"].ask_price == 60012.0


def test_unscoped_rewind_epoch_scoped_seq1_and_alias_channel_isolation():
    """Workstream B: Block 100->50->51 unscoped rewind, unscoped seq=1 on epoch channel, and alias split."""
    state = ObserverState()

    # 1. Unscoped 100 -> 50 -> 51 must keep 50 and 51 rejected
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "feed_seq": 100,
            "bid": 65000.0,
            "ask": 65002.0,
        }
    )
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "feed_seq": 50,
            "bid": 10000.0,
            "ask": 10002.0,
        }
    )
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "feed_seq": 51,
            "bid": 10001.0,
            "ask": 10003.0,
        }
    )
    b_quote = state.quotes["BTC-USDT"]["BINANCE"]
    assert state.health["BINANCE"].out_of_order == 2
    assert b_quote.bid_price == 65000.0
    assert b_quote.ask_price == 65002.0

    # Valid seq 101 resumes normal updates
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "feed_seq": 101,
            "bid": 65005.0,
            "ask": 65007.0,
        }
    )
    assert b_quote.bid_price == 65005.0
    assert state.health["BINANCE"].gaps == 0
    assert state.health["BINANCE"].last_feed_seq == 101

    # 2. Epoch-scoped channel + alias symbols (XBT/USD vs XBT-USD) + unscoped seq=1
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "XBT/USD",
            "epoch_id": 2,
            "feed_seq": 100,
            "bid": 60000.0,
            "ask": 60004.0,
        }
    )
    # Unscoped seq=1 arrives on epoch-scoped channel -> must NOT trigger DEGRADED_UNSCOPED_RESET
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "XBT/USD",
            "feed_seq": 1,
            "bid": 1000.0,
            "ask": 1001.0,
        }
    )
    # Stale epoch on alias XBT-USD -> hits same normalized default:BTC-USD channel and rejects
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "XBT-USD",
            "epoch_id": 1,
            "feed_seq": 1,
            "bid": 2000.0,
            "ask": 2001.0,
        }
    )
    k_quote = state.quotes["BTC-USD"]["KRAKEN"]
    assert k_quote.bid_price == 60000.0
    assert k_quote.ask_price == 60004.0
    assert state.health["KRAKEN"].degraded_resets == 0
    assert state.health["KRAKEN"].epoch_unavailable == 1
    assert state.health["KRAKEN"].stale_epoch_packets == 1
    assert list(state.health["KRAKEN"].channels.keys()) == ["default:BTC-USD"]


def test_negative_feed_seq_mid_price_overflow_and_normalized_subscription_filter():
    """Workstream B: Negative feed_seq duplicates, extreme float mid_price, and normalized subscription filter."""
    from observer.state import VenueQuote

    # Overflow-safe mid_price
    q_ext = VenueQuote(
        venue="BINANCE",
        symbol="BTC-USDT",
        bid_price=1e308,
        ask_price=1e308,
        bid_size=1.0,
        ask_size=1.0,
    )
    assert math.isfinite(q_ext.mid_price)
    assert q_ext.mid_price == 1e308

    # Negative feed_seq initializes once and detects duplicates
    st = ObserverState(subscription_filter="BTC-USDT")
    st.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "seq": 1,
            "feed_seq": -5,
            "bid": 60000.0,
            "ask": 60001.0,
        }
    )
    st.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "seq": 2,
            "feed_seq": -5,
            "bid": 50000.0,
            "ask": 50001.0,
        }
    )
    assert st.health["BINANCE"].duplicates == 1
    assert st.quotes["BTC-USDT"]["BINANCE"].bid_price == 60000.0

    # Normalized subscription_filter="BTC-USDT" matches "BTCUSDT" transport gap (seq 2 -> seq 4 = gap 1)
    st.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "seq": 4,
            "feed_seq": -4,
            "bid": 60002.0,
            "ask": 60003.0,
        }
    )
    assert st.stream_health.gaps == 1
    assert st.stream_health.filtered_events == 0


def test_f01_unscoped_out_of_order_no_phantom_gaps_scenarios():
    """F-01 Regression: Unscoped out-of-order packets must not rewind reference or emit phantom feed_gaps."""
    # Scenario A: 100 -> 50 -> 51 -> 101
    ch_a = ChannelSequenceState(channel_key="default:BTC-USDT")
    assert ch_a.update(100) == SequenceDisposition.ACCEPTED
    assert ch_a.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a.update(51) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a.last_feed_seq == 100
    assert ch_a.max_accepted_seq == 100

    assert ch_a.update(101) == SequenceDisposition.ACCEPTED
    assert ch_a.feed_out_of_order == 2
    assert ch_a.feed_gaps == 0
    assert ch_a.last_feed_seq == 101
    assert ch_a.max_accepted_seq == 101

    # Scenario B: 1000 -> 100 -> 1001
    ch_b = ChannelSequenceState(channel_key="default:BTC-USDT")
    assert ch_b.update(1000) == SequenceDisposition.ACCEPTED
    assert ch_b.update(100) == SequenceDisposition.OUT_OF_ORDER
    assert ch_b.last_feed_seq == 1000
    assert ch_b.max_accepted_seq == 1000

    assert ch_b.update(1001) == SequenceDisposition.ACCEPTED
    assert ch_b.feed_out_of_order == 1
    assert ch_b.feed_gaps == 0
    assert ch_b.last_feed_seq == 1001
    assert ch_b.max_accepted_seq == 1001


def test_f01_genuine_gaps_epoch_scoped_and_unavailable_sequences():
    """F-01 Regression: Genuine missing sequences still count exact gaps after OOO, in epochs, and with None."""
    # 1. Genuine missing sequences after unscoped OOO packets: 100 -> 50 -> 51 -> 105 misses only 101..104 (4 gaps)
    ch_gap = ChannelSequenceState(channel_key="default:BTC-USD")
    assert ch_gap.update(100) == SequenceDisposition.ACCEPTED
    assert ch_gap.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_gap.update(51) == SequenceDisposition.OUT_OF_ORDER
    assert ch_gap.update(105) == SequenceDisposition.ACCEPTED
    assert ch_gap.feed_out_of_order == 2
    assert ch_gap.feed_gaps == 4
    assert ch_gap.last_feed_seq == 105
    assert ch_gap.max_accepted_seq == 105

    # 2. Duplicate followed by direct genuine forward gap without OOO: 100 -> 100 (dup) -> 105 -> 4 gaps
    v = VenueHealth(venue="COINBASE")
    assert v.update_sequence(100) == SequenceDisposition.ACCEPTED
    assert v.update_sequence(100) == SequenceDisposition.DUPLICATE
    assert v.update_sequence(105) == SequenceDisposition.ACCEPTED
    assert v.feed_duplicates == 1
    assert v.feed_gaps == 4
    assert v.last_feed_seq == 105

    # 3. Epoch-scoped continuity, OOO, and genuine gaps: 100 -> 50 -> 101 -> 104 -> 1 OOO, 2 gaps
    ch_ep = ChannelSequenceState(
        channel_key="gw1:ETH-USD", gateway_id="gw1", symbol="ETH-USD"
    )
    assert ch_ep.update(100, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch_ep.update(50, epoch_id=1) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(101, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch_ep.feed_gaps == 0
    assert ch_ep.update(104, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch_ep.feed_out_of_order == 1
    assert ch_ep.feed_gaps == 2
    assert ch_ep.max_accepted_seq == 104

    # 4. Unavailable sequence (None) interleaved with continuous sequence: 100 -> None -> 101 -> 0 gaps
    v_unavail = VenueHealth(venue="KRAKEN")
    assert v_unavail.update_sequence(100) == SequenceDisposition.ACCEPTED
    assert v_unavail.update_sequence(None) == SequenceDisposition.UNAVAILABLE
    assert v_unavail.feed_seq_unavailable == 1
    assert v_unavail.update_sequence(101) == SequenceDisposition.ACCEPTED
    assert v_unavail.feed_gaps == 0
    assert v_unavail.last_feed_seq == 101


def test_f03_repeated_delayed_out_of_order_classified_as_duplicate():
    """F-03 Regression: Repeated delayed packet (100 -> 50 -> 50) must count 1 OOO and 1 DUPLICATE without rewind."""
    # 1. Unscoped ChannelSequenceState: 100 -> 50 -> 50 -> 101
    ch = ChannelSequenceState(channel_key="default:BTC-USDT")
    assert ch.update(100) == SequenceDisposition.ACCEPTED
    assert ch.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch.last_feed_seq == 100
    assert ch.max_accepted_seq == 100
    assert ch.last_observed_seq == 50

    assert ch.update(50) == SequenceDisposition.DUPLICATE
    assert ch.feed_out_of_order == 1
    assert ch.feed_duplicates == 1
    assert ch.feed_gaps == 0
    assert ch.last_feed_seq == 100
    assert ch.max_accepted_seq == 100
    assert ch.last_observed_seq == 50

    assert ch.update(101) == SequenceDisposition.ACCEPTED
    assert ch.feed_gaps == 0
    assert ch.last_feed_seq == 101
    assert ch.max_accepted_seq == 101
    assert ch.last_observed_seq == 101

    # 2. Epoch-scoped repeated delayed packet + high-water duplicate after OOO: 100 -> 50 -> 50 -> 100 -> 101
    ch_ep = ChannelSequenceState(
        channel_key="gw1:BTC-USD", gateway_id="gw1", symbol="BTC-USD"
    )
    assert ch_ep.update(100, epoch_id=2) == SequenceDisposition.ACCEPTED
    assert ch_ep.update(50, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(50, epoch_id=2) == SequenceDisposition.DUPLICATE
    assert ch_ep.update(100, epoch_id=2) == SequenceDisposition.DUPLICATE
    assert ch_ep.update(101, epoch_id=2) == SequenceDisposition.ACCEPTED
    assert ch_ep.feed_out_of_order == 1
    assert ch_ep.feed_duplicates == 2
    assert ch_ep.feed_gaps == 0
    assert ch_ep.last_feed_seq == 101
    assert ch_ep.max_accepted_seq == 101

    # 3. End-to-end ObserverState market quote protection across 100 -> 50 -> 50 -> 101
    st = ObserverState()
    st.process_event(
        {
            "venue": "DATABENTO",
            "symbol": "BTC-USD",
            "feed_seq": 100,
            "bid": 65000.0,
            "ask": 65002.0,
        }
    )
    st.process_event(
        {
            "venue": "DATABENTO",
            "symbol": "BTC-USD",
            "feed_seq": 50,
            "bid": 10000.0,
            "ask": 10001.0,
        }
    )
    st.process_event(
        {
            "venue": "DATABENTO",
            "symbol": "BTC-USD",
            "feed_seq": 50,
            "bid": 10002.0,
            "ask": 10003.0,
        }
    )
    st.process_event(
        {
            "venue": "DATABENTO",
            "symbol": "BTC-USD",
            "feed_seq": 101,
            "bid": 65005.0,
            "ask": 65007.0,
        }
    )

    q = st.quotes["BTC-USD"]["DATABENTO"]
    assert q.bid_price == 65005.0
    assert q.ask_price == 65007.0
    assert st.health["DATABENTO"].out_of_order == 1
    assert st.health["DATABENTO"].duplicates == 1
    assert st.health["DATABENTO"].gaps == 0
    assert st.health["DATABENTO"].last_feed_seq == 101
    assert st.health["DATABENTO"].channels["default:BTC-USD"].max_accepted_seq == 101


def test_f03_nonconsecutive_delayed_duplicate_and_window_eviction():
    """Verify bounded 4096-bit sliding bitmap catches non-consecutive duplicates, evicts old history, and preserves resets."""
    # A & B: Non-consecutive delayed duplicate 100 -> 50 -> 51 -> 50 -> 101
    ch_ab = ChannelSequenceState(channel_key="default:BTC-USDT")
    assert ch_ab.update(100) == SequenceDisposition.ACCEPTED
    assert ch_ab.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ab.update(51) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ab.update(50) == SequenceDisposition.DUPLICATE
    assert ch_ab.feed_out_of_order == 2
    assert ch_ab.feed_duplicates == 1
    assert ch_ab.feed_gaps == 0
    assert ch_ab.max_accepted_seq == 100
    assert ch_ab.last_feed_seq == 100

    assert ch_ab.update(101) == SequenceDisposition.ACCEPTED
    assert ch_ab.feed_out_of_order == 2
    assert ch_ab.feed_duplicates == 1
    assert ch_ab.feed_gaps == 0
    assert ch_ab.max_accepted_seq == 101
    assert ch_ab.last_feed_seq == 101
    assert ch_ab._seen_bitmap.bit_length() <= 4096

    # D: Repeated high-water sequence after OOO: 100 -> 50 -> 100
    ch_hw = ChannelSequenceState(channel_key="default:BTC-USDT")
    assert ch_hw.update(100) == SequenceDisposition.ACCEPTED
    assert ch_hw.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_hw.update(100) == SequenceDisposition.DUPLICATE
    assert ch_hw.feed_out_of_order == 1
    assert ch_hw.feed_duplicates == 1
    assert ch_hw.feed_gaps == 0

    # E: Genuine missing sequences after non-consecutive duplicate: 100 -> 50 -> 51 -> 50 -> 105
    ch_gap = ChannelSequenceState(channel_key="default:BTC-USD")
    assert ch_gap.update(100) == SequenceDisposition.ACCEPTED
    assert ch_gap.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_gap.update(51) == SequenceDisposition.OUT_OF_ORDER
    assert ch_gap.update(50) == SequenceDisposition.DUPLICATE
    assert ch_gap.update(105) == SequenceDisposition.ACCEPTED
    assert ch_gap.feed_out_of_order == 2
    assert ch_gap.feed_duplicates == 1
    assert ch_gap.feed_gaps == 4

    # F: Window boundary, shifting, eviction beyond 4096 positions, and bounded size on huge jumps
    ch_win = ChannelSequenceState(channel_key="default:ETH-USD")
    assert ch_win.update(100) == SequenceDisposition.ACCEPTED
    assert ch_win.update(50) == SequenceDisposition.OUT_OF_ORDER
    # Advance to high_water = 4145 (offset = 4145 - 50 = 4095 < 4096 -> still in window!)
    assert ch_win.update(4145) == SequenceDisposition.ACCEPTED
    assert ch_win._seen_bitmap.bit_length() == 4096
    assert ch_win.update(50) == SequenceDisposition.DUPLICATE
    assert ch_win.feed_duplicates == 1

    # Advance 1 more to high_water = 4146 (offset = 4146 - 50 = 4096 -> bit for 50 is evicted!)
    assert ch_win.update(4146) == SequenceDisposition.ACCEPTED
    assert ch_win._seen_bitmap.bit_length() <= 4096
    # Non-consecutive evicted sequence 50 must classify as OUT_OF_ORDER, not confirmed DUPLICATE
    assert ch_win.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_win.feed_out_of_order == 2
    assert ch_win.feed_duplicates == 1

    # Immediate repeat of an out-of-window sequence still classifies as DUPLICATE via last_observed_seq
    assert ch_win.update(50) == SequenceDisposition.DUPLICATE
    assert ch_win.feed_duplicates == 2

    # Huge forward jump resets bitmap cleanly and stays bounded
    assert ch_win.update(1_000_000) == SequenceDisposition.ACCEPTED
    assert ch_win._seen_bitmap == 1
    assert ch_win._seen_bitmap.bit_length() == 1

    # G: Unscoped seq == 1 reset must NOT be misclassified as duplicate even when seq=1 is in bitmap
    ch_rst = ChannelSequenceState(channel_key="default:BTC-USD")
    assert ch_rst.update(1) == SequenceDisposition.ACCEPTED
    assert ch_rst.update(50) == SequenceDisposition.ACCEPTED
    assert (
        (ch_rst._seen_bitmap >> 49) & 1
    ) == 1  # seq 1 is still in the 4096-bit window
    assert ch_rst.update(1) == SequenceDisposition.ACCEPTED
    assert ch_rst.feed_resets == 1
    assert ch_rst.degraded_resets == 1
    assert ch_rst.feed_duplicates == 0
    assert ch_rst.max_accepted_seq == 1
    # Consecutive repeat right after reset IS a duplicate
    assert ch_rst.update(1) == SequenceDisposition.DUPLICATE
    assert ch_rst.feed_duplicates == 1

    # Epoch transition + snapshot reset clear old bitmap; stale epoch does not corrupt active bitmap
    ch_ep = ChannelSequenceState(
        channel_key="gw1:BTC-USD", gateway_id="gw1", symbol="BTC-USD"
    )
    assert ch_ep.update(1, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch_ep.update(100, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch_ep.update(50, epoch_id=1) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(51, epoch_id=1) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(50, epoch_id=1) == SequenceDisposition.DUPLICATE

    # Epoch 2 resets window: seq 50 (unseen in epoch 2) is OOO, not a false duplicate from epoch 1
    assert ch_ep.update(100, epoch_id=2) == SequenceDisposition.ACCEPTED
    assert ch_ep._seen_bitmap == 1
    # Stale epoch 1 seq 40 must not modify epoch 2 bitmap
    assert ch_ep.update(40, epoch_id=1) == SequenceDisposition.STALE_EPOCH
    assert ch_ep._seen_bitmap == 1
    assert ch_ep.update(50, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(40, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER

    # Snapshot resets window cleanly
    assert (
        ch_ep.update(10, epoch_id=2, is_snapshot=True) == SequenceDisposition.ACCEPTED
    )
    assert ch_ep._seen_bitmap == 1
    assert ch_ep.update(5, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER

    # Unavailable None and legacy seq == 0 preserve contracts (100 -> None -> 0 -> 50 -> 0 -> 101)
    ch_zero = ChannelSequenceState(channel_key="default:KRAKEN")
    assert ch_zero.update(100) == SequenceDisposition.ACCEPTED
    assert ch_zero.update(None) == SequenceDisposition.UNAVAILABLE
    assert ch_zero.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_zero.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_zero.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_zero.feed_out_of_order == 3
    assert ch_zero.feed_duplicates == 0
    assert ch_zero.last_feed_seq == 100
    assert ch_zero.max_accepted_seq == 100
    assert ch_zero.update(101) == SequenceDisposition.ACCEPTED
    assert ch_zero.feed_gaps == 0


def test_channelsequencestate_positional_constructor_compatibility():
    """R1 Regression: preserve exact origin/main 16-field positional constructor contract.

    Fields introduced by PR #20 (`max_accepted_seq`, `last_observed_seq`, `_seen_bitmap`)
    must be keyword-only and placed after `continuity_state`, preserving the exact
    16-parameter positional signature from origin/main.
    """
    import inspect

    sig = inspect.signature(ChannelSequenceState)
    params = list(sig.parameters.keys())
    expected_origin_fields = [
        "channel_key",
        "gateway_id",
        "symbol",
        "current_epoch_id",
        "last_feed_seq",
        "feed_gaps",
        "feed_duplicates",
        "feed_out_of_order",
        "feed_snapshots",
        "feed_resets",
        "stale_epoch_packets",
        "epoch_unavailable",
        "degraded_resets",
        "has_sequence_feed",
        "events_received",
        "continuity_state",
    ]
    assert params[: len(expected_origin_fields)] == expected_origin_fields, (
        f"Positional signature drifted from origin/main: {params[:16]!r}"
    )

    for idx, name in enumerate(expected_origin_fields):
        assert sig.parameters[name].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD, (
            f"{name} at index {idx} must stay POSITIONAL_OR_KEYWORD"
        )

    for kw_name in ("max_accepted_seq", "last_observed_seq", "_seen_bitmap"):
        assert sig.parameters[kw_name].kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{kw_name} must be KEYWORD_ONLY, got {sig.parameters[kw_name].kind.name}"
        )

    # Actual origin/main 6-arg positional call sets feed_gaps=5 at index 5
    ch = ChannelSequenceState("test", None, None, None, 100, 5)
    assert ch.last_feed_seq == 100
    assert ch.feed_gaps == 5
    assert ch.max_accepted_seq == -1
    assert ch.last_observed_seq == -1
    assert ch._seen_bitmap == 0

    # Full 16-arg origin/main positional construction maps every field accurately
    ch_full = ChannelSequenceState(
        "gw1:BTC-USD",
        "gw1",
        "BTC-USD",
        7,
        100,
        5,
        2,
        1,
        3,
        4,
        6,
        8,
        9,
        True,
        42,
        "HEALTHY_EPOCH_INITIALIZED",
    )
    assert ch_full.channel_key == "gw1:BTC-USD"
    assert ch_full.gateway_id == "gw1"
    assert ch_full.symbol == "BTC-USD"
    assert ch_full.current_epoch_id == 7
    assert ch_full.last_feed_seq == 100
    assert ch_full.feed_gaps == 5
    assert ch_full.feed_duplicates == 2
    assert ch_full.feed_out_of_order == 1
    assert ch_full.feed_snapshots == 3
    assert ch_full.feed_resets == 4
    assert ch_full.stale_epoch_packets == 6
    assert ch_full.epoch_unavailable == 8
    assert ch_full.degraded_resets == 9
    assert ch_full.has_sequence_feed is True
    assert ch_full.events_received == 42
    assert ch_full.continuity_state == "HEALTHY_EPOCH_INITIALIZED"
    assert ch_full.update(101, epoch_id=7) == SequenceDisposition.ACCEPTED
    assert ch_full.max_accepted_seq == 101

    # Keyword construction for new fields still works
    ch2 = ChannelSequenceState("test2", max_accepted_seq=99, last_observed_seq=42)
    assert ch2.max_accepted_seq == 99
    assert ch2.last_observed_seq == 42
    assert ch2.feed_gaps == 0


def test_venuehealth_positional_constructor_compatibility():
    """R2 Regression: preserve exact origin/main 12-field positional constructor contract.

    Fields introduced by PR #20 (`invalid_numeric_events`, `_lock`) must be keyword-only
    and appended after `_last_active_channel`, keeping `latencies_us` at index 9 and
    `channels` at index 10.
    """
    import collections
    import inspect

    sig = inspect.signature(VenueHealth)
    params = list(sig.parameters.keys())
    expected_origin_fields = [
        "venue",
        "status",
        "feed_type",
        "events_received",
        "drops",
        "reconnects",
        "last_seen_ts",
        "feed_seq_unavailable",
        "gateway_unscoped",
        "latencies_us",
        "channels",
        "_last_active_channel",
    ]
    assert params[: len(expected_origin_fields)] == expected_origin_fields, (
        f"VenueHealth signature drifted from origin/main: {params[:12]!r}"
    )
    assert params.index("latencies_us") == 9
    assert params.index("channels") == 10

    for kw_name in ("invalid_numeric_events", "_lock"):
        assert sig.parameters[kw_name].kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{kw_name} must be KEYWORD_ONLY, got {sig.parameters[kw_name].kind.name}"
        )

    lat_deque = collections.deque([12.5, 18.0], maxlen=100)
    custom_channels = {"gw1:BTC-USD": ChannelSequenceState("gw1:BTC-USD")}
    vh = VenueHealth(
        "COINBASE",
        "CONNECTED",
        "LIVE",
        10,
        1,
        2,
        12345.0,
        3,
        4,
        lat_deque,
        custom_channels,
        "gw1:BTC-USD",
    )
    assert vh.venue == "COINBASE"
    assert vh.status == "CONNECTED"
    assert vh.feed_type == "LIVE"
    assert vh.events_received == 10
    assert vh.drops == 1
    assert vh.reconnects == 2
    assert vh.last_seen_ts == 12345.0
    assert vh.feed_seq_unavailable == 3
    assert vh.gateway_unscoped == 4
    assert vh.latencies_us is lat_deque
    assert vh.channels is custom_channels
    assert vh._last_active_channel == "gw1:BTC-USD"
    assert vh.invalid_numeric_events == 0
    assert vh._lock is None

    vh_kw = VenueHealth("KRAKEN", invalid_numeric_events=7)
    assert vh_kw.invalid_numeric_events == 7
    assert vh_kw.drops is None
    assert vh_kw.reconnects is None


def test_legacy_sequence_zero_semantics_and_precedence():
    """Regression: preserve legacy seq==0 OUT_OF_ORDER semantics alongside F-01/F-03 bitmap tracking."""
    # A. Legacy zero semantics: 100 -> 0 -> 50 -> 0
    ch_a = ChannelSequenceState(channel_key="default:KRAKEN")
    assert ch_a.update(100) == SequenceDisposition.ACCEPTED
    assert ch_a.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a.feed_out_of_order == 3  # two zeros + one 50
    assert ch_a.feed_duplicates == 0
    assert ch_a.feed_gaps == 0
    assert ch_a.last_feed_seq == 100
    assert ch_a.max_accepted_seq == 100

    # Immediate repeated 0 after higher sequence must also remain OUT_OF_ORDER (no bitmap/last_observed_seq poisoning)
    ch_a2 = ChannelSequenceState(channel_key="default:KRAKEN")
    assert ch_a2.update(100) == SequenceDisposition.ACCEPTED
    assert ch_a2.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a2.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_a2.feed_out_of_order == 2
    assert ch_a2.feed_duplicates == 0

    # Zero packet must not erase/break non-zero bitmap duplicate detection: 100 -> 50 -> 0 -> 50
    ch_mix = ChannelSequenceState(channel_key="default:KRAKEN")
    assert ch_mix.update(100) == SequenceDisposition.ACCEPTED
    assert ch_mix.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_mix.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_mix.update(50) == SequenceDisposition.DUPLICATE
    assert ch_mix.feed_out_of_order == 2
    assert ch_mix.feed_duplicates == 1

    # B. Initial zero: 0 -> 0 (plus initial zero followed by higher then delayed zero: 0 -> 10 -> 0)
    ch_b = ChannelSequenceState(channel_key="default:COINBASE")
    assert ch_b.update(0) == SequenceDisposition.ACCEPTED
    assert ch_b.last_feed_seq == 0
    assert ch_b.max_accepted_seq == 0
    assert ch_b.update(0) == SequenceDisposition.DUPLICATE
    assert ch_b.feed_duplicates == 1
    assert ch_b.feed_out_of_order == 0

    ch_b2 = ChannelSequenceState(channel_key="default:COINBASE")
    assert ch_b2.update(0) == SequenceDisposition.ACCEPTED
    assert ch_b2.update(10) == SequenceDisposition.ACCEPTED
    assert ch_b2.feed_gaps == 9
    assert ch_b2.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_b2.feed_out_of_order == 1
    assert ch_b2.feed_duplicates == 0

    # C. Immediate high-water duplicate after zero: 100 -> 0 -> 100
    ch_c = ChannelSequenceState(channel_key="default:BINANCE")
    assert ch_c.update(100) == SequenceDisposition.ACCEPTED
    assert ch_c.update(0) == SequenceDisposition.OUT_OF_ORDER
    assert ch_c.update(100) == SequenceDisposition.DUPLICATE
    assert ch_c.feed_out_of_order == 1
    assert ch_c.feed_duplicates == 1
    assert ch_c.feed_gaps == 0
    assert ch_c.last_feed_seq == 100
    assert ch_c.max_accepted_seq == 100

    # D. Bitmap duplicate detection remains intact: 100 -> 50 -> 51 -> 50
    ch_d = ChannelSequenceState(channel_key="default:BTC-USDT")
    assert ch_d.update(100) == SequenceDisposition.ACCEPTED
    assert ch_d.update(50) == SequenceDisposition.OUT_OF_ORDER
    assert ch_d.update(51) == SequenceDisposition.OUT_OF_ORDER
    assert ch_d.update(50) == SequenceDisposition.DUPLICATE
    assert ch_d.feed_out_of_order == 2
    assert ch_d.feed_duplicates == 1
    assert ch_d.feed_gaps == 0

    # E. Reset marker remains intact: 1 -> 50 -> 1 -> 1
    ch_e = ChannelSequenceState(channel_key="default:ETH-USD")
    assert ch_e.update(1) == SequenceDisposition.ACCEPTED
    assert ch_e.update(50) == SequenceDisposition.ACCEPTED
    assert ch_e.feed_gaps == 48
    assert ch_e.update(1) == SequenceDisposition.ACCEPTED
    assert ch_e.feed_resets == 1
    assert ch_e.degraded_resets == 1
    assert ch_e.continuity_state == "DEGRADED_UNSCOPED_RESET"
    assert ch_e.update(1) == SequenceDisposition.DUPLICATE
    assert ch_e.feed_duplicates == 1
    assert ch_e.feed_resets == 1

    # F. Epoch-scoped seq == 0 also preserves legacy contract: 0 -> 0 is DUPLICATE, 100 -> 0 -> 50 -> 0 is OUT_OF_ORDER
    ch_ep = ChannelSequenceState(
        channel_key="gw1:BTC-USD", gateway_id="gw1", symbol="BTC-USD"
    )
    assert ch_ep.update(0, epoch_id=1) == SequenceDisposition.ACCEPTED
    assert ch_ep.update(0, epoch_id=1) == SequenceDisposition.DUPLICATE
    assert ch_ep.update(100, epoch_id=2) == SequenceDisposition.ACCEPTED
    assert ch_ep.update(0, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(50, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(0, epoch_id=2) == SequenceDisposition.OUT_OF_ORDER
    assert ch_ep.update(50, epoch_id=2) == SequenceDisposition.DUPLICATE
    assert ch_ep.feed_out_of_order == 3
    assert ch_ep.feed_duplicates == 2
