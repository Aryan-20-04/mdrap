"""Comprehensive tests for Observer state tracking, microstructure math, and feed integrity."""

import os
import sys
import threading
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
    """Verify ticker normalization strictly separates USD from USDT and rejects derivatives/wrapped tickers."""
    # Canonical BTC-USDT variants
    for sym in (
        "BTCUSDT",
        "BTC-USDT",
        "BTC/USDT",
        "BTC_USDT",
        "XBTUSDT",
        "XBT-USDT",
        "XBT/USDT",
        "XBT_USDT",
        "btcusdt",
    ):
        assert normalize_asset(sym) == "BTC-USDT", f"Expected BTC-USDT for {sym}"

    # Canonical BTC-USD variants
    for sym in (
        "BTC-USD",
        "BTC/USD",
        "BTC_USD",
        "BTCUSD",
        "XBT/USD",
        "XBT-USD",
        "XBT_USD",
        "XBTUSD",
        "BTC",
        "XBT",
        "xbt/usd",
    ):
        assert normalize_asset(sym) == "BTC-USD", f"Expected BTC-USD for {sym}"

    # Canonical ETH-USDT and ETH-USD variants
    for sym in ("ETHUSDT", "ETH-USDT", "ETH/USDT", "ETH_USDT", "eth-usdt"):
        assert normalize_asset(sym) == "ETH-USDT", f"Expected ETH-USDT for {sym}"
    for sym in ("ETH-USD", "ETH/USD", "ETH_USD", "ETHUSD", "ETH", "eth"):
        assert normalize_asset(sym) == "ETH-USD", f"Expected ETH-USD for {sym}"

    # Wrapped assets, derivatives, forks, and non-USD/USDT pairs must NOT be conflated
    for non_core in (
        "WBTC-USD",
        "WBTCUSDT",
        "BTCUSDT-PERP",
        "BTC-USD-PERP",
        "ETHW-USD",
        "BTC-EUR",
        "BTC-USDC",
        "SOL-USD",
    ):
        assert normalize_asset(non_core) == non_core, (
            f"Unexpected normalization for {non_core}"
        )


def test_non_core_wrapped_and_perp_symbols_do_not_pollute_core_books():
    """Verify wrapped/perp/fiat-cross symbols do not mutate BTC-USD/BTC-USDT/ETH-USD books."""
    state = ObserverState()
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "bid": 60000.0,
            "ask": 60002.0,
            "feed_seq": 1,
        }
    )

    for sym in ("WBTC-USD", "BTCUSDT-PERP", "ETHW-USD", "BTC-EUR", "SOL-USD"):
        state.process_event(
            {
                "venue": "COINBASE",
                "symbol": sym,
                "bid": 100.0,
                "ask": 101.0,
                "feed_seq": 1,
            }
        )

    q_btc = state.quotes["BTC-USD"]["COINBASE"]
    assert q_btc.bid_price == 60000.0
    assert q_btc.ask_price == 60002.0
    assert state.quotes["BTC-USDT"]["COINBASE"].bid_price == 0.0
    assert state.quotes["ETH-USD"]["COINBASE"].bid_price == 0.0


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
    state.process_event(
        {
            "venue": "BINANCE",
            "symbol": "BTCUSDT",
            "bid": 60000.0,
            "bid_size": 2.5,
            "ask": 60005.0,
            "ask_size": 1.8,
            "sequence": 1,
        }
    )

    # Coinbase USD quote
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "bid": 60001.0,
            "bid_size": 3.0,
            "ask": 60006.0,
            "ask_size": 2.0,
            "sequence": 100,
        }
    )

    # Kraken USD quote
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "XBT/USD",
            "bid": 59999.0,
            "bid_size": 1.0,
            "ask": 60003.0,
            "ask_size": 4.0,
            "sequence": 50,
        }
    )

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
    converted_bid, _, converted_ask, _ = state.get_consolidated_nbbo(
        "BTC-USDT", usdt_fx_rate=0.999
    )
    assert pytest.approx(converted_bid, abs=1e-2) == 60000.0 * 0.999  # 59940.0
    assert pytest.approx(converted_ask, abs=1e-2) == 60005.0 * 0.999  # 59944.995


def test_lee_ready_trade_aggressor_inference():
    """Verify trade aggressor side using Lee-Ready tick rule."""
    state = ObserverState()

    # Seed top-of-book for BTC-USD: Bid=60000.0, Ask=60004.0 (Mid=60002.0)
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "bid": 60000.0,
            "ask": 60004.0,
            "sequence": 1,
        }
    )

    # 1. Trade at or above Ask -> BUY
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "event_type": "TRADE",
            "price": 60004.0,
            "quantity": 1.0,
            "sequence": 2,
        }
    )
    assert state.trades["BTC-USD"][0].side == "BUY"

    # 2. Trade at or below Bid -> SELL
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "event_type": "TRADE",
            "price": 60000.0,
            "quantity": 0.5,
            "sequence": 3,
        }
    )
    assert state.trades["BTC-USD"][0].side == "SELL"

    # 3. Trade between Bid and Ask with Uptick -> BUY
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "event_type": "TRADE",
            "price": 60002.0,  # 60002 > 60000 (uptick)
            "quantity": 0.2,
            "sequence": 4,
        }
    )
    assert state.trades["BTC-USD"][0].side == "BUY"

    # 4. Trade between Bid and Ask with Downtick -> SELL
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "event_type": "TRADE",
            "price": 60001.0,  # 60001 < 60002 (downtick)
            "quantity": 0.3,
            "sequence": 5,
        }
    )
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

    # Gap: 16 after accepted high-water 11 (delayed 9 must not rewind gap baseline; missing 12..15 -> gap of 4)
    state.process_event({"venue": "DATABENTO", "symbol": "BTC-USD", "sequence": 16})
    assert state.health["DATABENTO"].gaps == 4
    assert state.health["DATABENTO"].last_feed_seq == 16
    assert state.health["DATABENTO"].channels["default:BTC-USD"].max_accepted_seq == 16


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
        state.process_event(
            {
                "venue": "ITCH_SBE",
                "symbol": "ETH-USD",
                "event_type": "TRADE",
                "price": 3000.0 + i,
                "quantity": 0.5 * (i + 1),
                "sequence": i + 1,
            }
        )

    assert len(state.trades["ETH-USD"]) == 5
    # Most recent trade at index 0
    assert state.trades["ETH-USD"][0].price == 3009.0
    assert state.trades["ETH-USD"][-1].price == 3005.0


def test_stale_epoch_out_of_order_and_duplicate_do_not_poison_quotes_or_nbbo():
    """Verify stale-epoch, out-of-order, and duplicate packets never mutate live quotes, NBBO, microprice, or trades."""
    state = ObserverState()

    # 1. Establish valid epoch 2 quotes on COINBASE and KRAKEN for BTC-USD
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 100,
            "bid": 60000.0,
            "bid_size": 3.0,
            "ask": 60004.0,
            "ask_size": 1.0,
            "timestamp": 1000.0,
        }
    )
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 50,
            "bid": 59998.0,
            "bid_size": 2.0,
            "ask": 60003.0,
            "ask_size": 4.0,
            "timestamp": 1000.0,
        }
    )
    # Record a valid trade at epoch 2, seq 101 on COINBASE
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "event_type": "TRADE",
            "epoch_id": 2,
            "feed_seq": 101,
            "price": 60004.0,
            "quantity": 0.5,
            "timestamp": 1001.0,
        }
    )

    cb_quote = state.quotes["BTC-USD"]["COINBASE"]
    kr_quote = state.quotes["BTC-USD"]["KRAKEN"]
    baseline_cb_micro = cb_quote.micro_price
    baseline_nbbo = state.get_consolidated_nbbo("BTC-USD")
    assert baseline_nbbo == (60000.0, 3.0, 60003.0, 4.0)
    assert len(state.trades["BTC-USD"]) == 1

    # 2. Submit stale epoch 1 quote with radically different prices/sizes (and stale snapshot)
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 1,
            "feed_seq": 999,
            "bid": 10000.0,
            "bid_size": 500.0,
            "ask": 99000.0,
            "ask_size": 500.0,
            "timestamp": 2000.0,
        }
    )
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 1,
            "feed_seq": 1000,
            "is_snapshot": True,
            "bid": 12000.0,
            "ask": 12001.0,
            "timestamp": 2001.0,
        }
    )
    assert state.health["COINBASE"].stale_epoch_packets == 2
    assert cb_quote.bid_price == 60000.0
    assert cb_quote.ask_price == 60004.0
    assert cb_quote.bid_size == 3.0
    assert cb_quote.ask_size == 1.0
    assert cb_quote.last_update_ts == 1001.0
    assert cb_quote.micro_price == baseline_cb_micro
    assert state.get_consolidated_nbbo("BTC-USD") == baseline_nbbo

    # 3. Submit lower feed_seq within epoch 2 (out-of-order)
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 95,
            "bid": 75000.0,
            "bid_size": 99.0,
            "ask": 75005.0,
            "ask_size": 99.0,
            "timestamp": 2002.0,
        }
    )
    assert state.health["COINBASE"].out_of_order == 1
    assert state.health["COINBASE"].last_feed_seq == 101  # Must not rewind
    assert cb_quote.bid_price == 60000.0
    assert cb_quote.ask_price == 60004.0
    assert state.get_consolidated_nbbo("BTC-USD") == baseline_nbbo

    # 4. Submit duplicate feed_seq (both quote and trade)
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "event_type": "TRADE",
            "epoch_id": 2,
            "feed_seq": 101,
            "price": 12345.0,
            "quantity": 10.0,
            "bid": 12344.0,
            "ask": 12346.0,
            "timestamp": 2003.0,
        }
    )
    assert state.health["COINBASE"].duplicates == 1
    assert len(state.trades["BTC-USD"]) == 1
    assert state.trades["BTC-USD"][0].price == 60004.0
    assert cb_quote.bid_price == 60000.0
    assert state.get_consolidated_nbbo("BTC-USD") == baseline_nbbo

    # 5. KRAKEN channel remains independent and unaffected; COINBASE accepts higher epoch 2 seq
    state.process_event(
        {
            "venue": "KRAKEN",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 51,
            "bid": 59999.0,
            "bid_size": 2.5,
            "ask": 60002.0,
            "ask_size": 3.5,
        }
    )
    assert kr_quote.bid_price == 59999.0
    assert kr_quote.ask_price == 60002.0
    assert state.health["KRAKEN"].stale_epoch_packets == 0
    assert state.health["KRAKEN"].out_of_order == 0

    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "epoch_id": 2,
            "feed_seq": 102,
            "bid": 60001.0,
            "bid_size": 4.0,
            "ask": 60005.0,
            "ask_size": 2.0,
        }
    )
    assert cb_quote.bid_price == 60001.0
    assert cb_quote.ask_price == 60005.0
    assert state.health["COINBASE"].last_feed_seq == 102
    assert state.get_consolidated_nbbo("BTC-USD") == (60001.0, 4.0, 60002.0, 3.5)


def test_explicit_zero_sizes_preserved_in_process_event():
    """Verify explicit 0.0 bid_size/ask_size are preserved rather than replaced by default 1.0."""
    state = ObserverState()
    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "bid": 60000.0,
            "bid_size": 0.0,
            "ask": 60002.0,
            "ask_size": 5.0,
            "feed_seq": 1,
        }
    )
    q = state.quotes["BTC-USD"]["COINBASE"]
    assert q.bid_size == 0.0
    assert q.ask_size == 5.0
    # Micro-price with Q_bid=0, Q_ask=5 equals P_bid = 60000.0
    assert q.micro_price == 60000.0

    state.process_event(
        {
            "venue": "COINBASE",
            "symbol": "BTC-USD",
            "bid": 60000.0,
            "bid_size": 0.0,
            "ask": 60002.0,
            "ask_size": 0.0,
            "feed_seq": 2,
        }
    )
    assert q.ask_size == 0.0
    # Both sizes zero -> midpoint fallback 60001.0
    assert q.micro_price == 60001.0


def test_concurrent_ingestion_health_snapshots_throughput_and_channels():
    """Workstream A: Stress concurrent process_event() against snapshot_health(), get_throughput_eps(), NBBO, and check_all_stale()."""
    state = ObserverState(max_trades=50, stale_threshold_s=0.05)
    errors: list[BaseException] = []
    stop_flag = threading.Event()
    total_ingested = (
        6000  # Exceeds _event_timestamps maxlen=5000 to stress deque rotation
    )

    def writer_worker():
        try:
            for i in range(1, total_ingested + 1):
                gw = f"gw_{i % 60}"
                sym = "BTCUSDT" if (i % 2 == 0) else f"ALT{i % 150}-USD"
                state.process_event(
                    {
                        "venue": "BINANCE",
                        "symbol": sym,
                        "gateway_id": gw,
                        "event_type": "TRADE" if (i % 5 == 0) else "TICK",
                        "price": 65000.0 + (i % 20),
                        "bid": 64999.0 + (i % 20),
                        "bid_size": 1.5,
                        "ask": 65001.0 + (i % 20),
                        "ask_size": 2.0,
                        "feed_seq": i,
                        "seq": i,
                    }
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop_flag.set()

    def reader_worker():
        try:
            while not stop_flag.is_set():
                snap = state.snapshot_health()
                dash = state.snapshot_dashboard_data()
                eps = state.get_throughput_eps()
                _ = state.get_consolidated_nbbo("BTC-USDT")
                state.check_all_stale()
                # Also exercise direct VenueHealth property snapshots concurrently
                _ = state.health["BINANCE"].feed_gaps
                assert eps >= 0.0
                assert "BINANCE" in snap["venues"]
                assert "BTC-USDT" in dash["nbbo"]
        except BaseException as exc:
            errors.append(exc)

    w = threading.Thread(target=writer_worker)
    r1 = threading.Thread(target=reader_worker)
    r2 = threading.Thread(target=reader_worker)
    w.start()
    r1.start()
    r2.start()
    w.join(timeout=15.0)
    stop_flag.set()
    r1.join(timeout=5.0)
    r2.join(timeout=5.0)

    assert not errors, f"Concurrency errors encountered: {errors}"
    final_snap = state.snapshot_health()
    assert final_snap["venues"]["BINANCE"]["events"] == total_ingested
    assert final_snap["stream"]["events"] == total_ingested
    # Decoupled snapshot mutation check: mutating returned snapshot must not affect internal state
    final_snap["venues"]["BINANCE"]["events"] = -999
    assert state.health["BINANCE"].events_received == total_ingested
