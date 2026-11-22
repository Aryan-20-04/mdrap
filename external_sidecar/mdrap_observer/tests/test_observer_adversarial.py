"""Adversarial and boundary fault injection tests for MDRAP Observer."""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.state import ObserverState, VenueQuote


def test_crossed_book_detection():
    """Verify inverted books (bid > ask) and locked books (bid == ask) are flagged."""
    # Normal book
    q_normal = VenueQuote(venue="BINANCE", symbol="BTC-USDT", bid_price=60000.0, ask_price=60001.0)
    assert not q_normal.is_crossed
    assert q_normal.spread == 1.0

    # Locked book (bid == ask)
    q_locked = VenueQuote(venue="BINANCE", symbol="BTC-USDT", bid_price=60000.0, ask_price=60000.0)
    assert q_locked.is_crossed
    assert q_locked.spread == 0.0

    # Inverted / crossed book (bid > ask)
    q_crossed = VenueQuote(venue="BINANCE", symbol="BTC-USDT", bid_price=60005.0, ask_price=60000.0)
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
    state.process_event({"venue": "UNKNOWN_EXCHANGE", "symbol": "BTC-USD", "price": 60000.0})

    # Non-numeric string prices and sequences
    state.process_event({
        "venue": "BINANCE",
        "symbol": "BTCUSDT",
        "bid": "not_a_float",
        "ask": None,
        "sequence": "invalid_int",
    })

    # Negative price values
    state.process_event({
        "venue": "KRAKEN",
        "symbol": "BTC-USD",
        "bid": -100.0,
        "ask": -50.0,
        "sequence": 1,
    })

    # Health must still track the event received from valid venue
    assert state.health["BINANCE"].events_received >= 1
    assert state.health["KRAKEN"].events_received >= 1
