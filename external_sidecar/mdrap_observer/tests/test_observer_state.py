"""Comprehensive tests for Observer state tracking, microstructure math, and feed integrity."""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.state import (
    ObserverState,
    VenueQuote,
    Level,
    VENUES,
    CORE_ASSETS,
    normalize_asset,
)


def test_normalize_asset_preserves_currency():
    """Verify ticker normalization strictly separates USD from USDT."""
    assert normalize_asset("BTCUSDT") == "BTC-USDT"
    assert normalize_asset("BTC-USD") == "BTC-USD"
    assert normalize_asset("XBT/USD") == "BTC-USD"
    assert normalize_asset("ETHUSDT") == "ETH-USDT"
    assert normalize_asset("ETH-USD") == "ETH-USD"
    assert normalize_asset("BTC") == "BTC-USD"
    assert normalize_asset("ETH") == "ETH-USD"
    # Unmonitored symbol preserved as-is
    assert normalize_asset("SOL-USD") == "SOL-USD"


def test_micro_price_calculation():
    """Verify volume-weighted micro-price matches exact mathematical formulation."""
    # P_micro = (P_ask * Q_bid + P_bid * Q_ask) / (Q_bid + Q_ask)
    q = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=10.0,
        ask_price=102.0,
        ask_size=2.0,
    )
    # Mid-price is (100 + 102) / 2 = 101.0
    assert q.mid_price == 101.0
    # Micro-price: (102.0 * 10.0 + 100.0 * 2.0) / 12.0 = 1220.0 / 12.0 = 101.666666...
    expected_micro = (102.0 * 10.0 + 100.0 * 2.0) / 12.0
    assert pytest.approx(q.micro_price, abs=1e-5) == expected_micro
    assert q.micro_price > q.mid_price  # Higher bid queue skews price toward ask

    # Balanced book: micro-price == mid-price
    q_balanced = VenueQuote(
        venue="COINBASE",
        symbol="BTC-USD",
        bid_price=100.0,
        bid_size=5.0,
        ask_price=102.0,
        ask_size=5.0,
    )
    assert q_balanced.micro_price == 101.0

    # Spread calculation
    assert q.spread == 2.0
    # Spread bps = (2.0 / 101.0) * 10000 = 198.0198 bps
    assert pytest.approx(q.spread_bps, abs=1e-2) == 198.02


def test_state_currency_pair_isolation_and_fx_conversion():
    """Verify BTC-USD and BTC-USDT are kept isolated in separate order books."""
    state = ObserverState()

    # Binance USDT quote
    state.process_event({
        "venue": "BINANCE",
        "symbol": "BTCUSDT",
        "bid": 60000.0,
        "bid_size": 2.5,
        "ask": 60005.0,
        "ask_size": 1.8,
        "sequence": 1,
    })

    # Coinbase USD quote
    state.process_event({
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "bid": 60001.0,
        "bid_size": 3.0,
        "ask": 60006.0,
        "ask_size": 2.0,
        "sequence": 100,
    })

    # Kraken USD quote
    state.process_event({
        "venue": "KRAKEN",
        "symbol": "XBT/USD",
        "bid": 59999.0,
        "bid_size": 1.0,
        "ask": 60003.0,
        "ask_size": 4.0,
        "sequence": 50,
    })

    # USD NBBO should only consider Coinbase and Kraken
    best_bid, b_sz, best_ask, a_sz = state.get_consolidated_nbbo("BTC-USD")
    assert best_bid == 60001.0  # Coinbase
    assert b_sz == 3.0
    assert best_ask == 60003.0  # Kraken
    assert a_sz == 4.0

    # USDT NBBO should only consider Binance
    usdt_bid, usdt_b_sz, usdt_ask, usdt_a_sz = state.get_consolidated_nbbo("BTC-USDT")
    assert usdt_bid == 60000.0  # Binance
    assert usdt_b_sz == 2.5
    assert usdt_ask == 60005.0  # Binance
    assert usdt_a_sz == 1.8

    # When explicit FX conversion is applied (e.g. 1 USDT = 0.999 USD)
    converted_bid, _, converted_ask, _ = state.get_consolidated_nbbo("BTC-USDT", usdt_fx_rate=0.999)
    assert pytest.approx(converted_bid, abs=1e-2) == 60000.0 * 0.999  # 59940.0
    assert pytest.approx(converted_ask, abs=1e-2) == 60005.0 * 0.999  # 59944.995


def test_lee_ready_trade_aggressor_inference():
    """Verify trade aggressor side using Lee-Ready tick rule."""
    state = ObserverState()

    # Seed top-of-book for BTC-USD: Bid=60000.0, Ask=60004.0 (Mid=60002.0)
    state.process_event({
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "bid": 60000.0,
        "ask": 60004.0,
        "sequence": 1,
    })

    # 1. Trade at or above Ask -> BUY
    state.process_event({
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "event_type": "TRADE",
        "price": 60004.0,
        "quantity": 1.0,
        "sequence": 2,
    })
    assert state.trades["BTC-USD"][0].side == "BUY"

    # 2. Trade at or below Bid -> SELL
    state.process_event({
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "event_type": "TRADE",
        "price": 60000.0,
        "quantity": 0.5,
        "sequence": 3,
    })
    assert state.trades["BTC-USD"][0].side == "SELL"

    # 3. Trade between Bid and Ask with Uptick -> BUY
    state.process_event({
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "event_type": "TRADE",
        "price": 60002.0,  # 60002 > 60000 (uptick)
        "quantity": 0.2,
        "sequence": 4,
    })
    assert state.trades["BTC-USD"][0].side == "BUY"

    # 4. Trade between Bid and Ask with Downtick -> SELL
    state.process_event({
        "venue": "COINBASE",
        "symbol": "BTC-USD",
        "event_type": "TRADE",
        "price": 60001.0,  # 60001 < 60002 (downtick)
        "quantity": 0.3,
        "sequence": 5,
    })
    assert state.trades["BTC-USD"][0].side == "SELL"


def test_feed_sequence_integrity_gaps_duplicates_out_of_order():
    """Verify tracking of sequence gaps, duplicates, and out-of-order packets."""
    state = ObserverState()

    # Normal: 10 -> 11
    state.process_event({"venue": "DATABENTO", "symbol": "BTC-USD", "sequence": 10})
    state.process_event({"venue": "DATABENTO", "symbol": "BTC-USD", "sequence": 11})
    assert state.health["DATABENTO"].events_received == 2
    assert state.health["DATABENTO"].gaps == 0
    assert state.health["DATABENTO"].duplicates == 0
    assert state.health["DATABENTO"].out_of_order == 0

    # Duplicate: 11 again
    state.process_event({"venue": "DATABENTO", "symbol": "BTC-USD", "sequence": 11})
    assert state.health["DATABENTO"].duplicates == 1

    # Out of order: 9 (< 11)
    state.process_event({"venue": "DATABENTO", "symbol": "BTC-USD", "sequence": 9})
    assert state.health["DATABENTO"].out_of_order == 1

    # Gap: 16 (gap of 16 - 9 - 1 = 6)
    state.process_event({"venue": "DATABENTO", "symbol": "BTC-USD", "sequence": 16})
    assert state.health["DATABENTO"].gaps == 6


def test_stale_feed_detection():
    """Verify feed transitioning to STALE when heartbeats or updates exceed threshold."""
    state = ObserverState(stale_threshold_s=0.1)

    state.process_event({"venue": "KRAKEN", "symbol": "BTC-USD", "sequence": 1})
    assert state.health["KRAKEN"].status == "CONNECTED"

    # Sleep past stale threshold
    time.sleep(0.15)
    state.check_all_stale()
    assert state.health["KRAKEN"].status == "STALE"

    # Recovery upon receiving new event
    state.process_event({"venue": "KRAKEN", "symbol": "BTC-USD", "sequence": 2})
    assert state.health["KRAKEN"].status == "CONNECTED"


def test_bounded_trades_tape():
    """Verify trades deque strictly maintains bounded size with most recent prints first."""
    state = ObserverState(max_trades=5)

    for i in range(10):
        state.process_event({
            "venue": "ITCH_SBE",
            "symbol": "ETH-USD",
            "event_type": "TRADE",
            "price": 3000.0 + i,
            "quantity": 0.5 * (i + 1),
            "sequence": i + 1,
        })

    assert len(state.trades["ETH-USD"]) == 5
    # Most recent trade at index 0
    assert state.trades["ETH-USD"][0].price == 3009.0
    assert state.trades["ETH-USD"][-1].price == 3005.0
