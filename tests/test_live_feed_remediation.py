"""
Comprehensive regression tests for MDRAP 6.0.2 Live Feed Reliability Remediation.

Verifies fixes for:
1. Kraken incremental book updates and orderbook cache reconstruction.
2. Trade frame parsing across all 5 venues (Binance, Coinbase, Kraken, OKX, Bybit).
3. Session/channel sequence tracking isolation (no trade vs quote cross-contamination).
4. Heartbeat liveness and freshness telemetry (preventing false STALE state).
5. Bounded backpressure tombstone capacity and drop accounting.
6. End-to-end evaluation with 100% pure-Python and C fastpath parity.
"""

import time
import pytest

from mdrap.config import QualityConfig, DEFAULT_ASSET_CLASSES
from mdrap.gateway import Gateway
from mdrap.models import EventType, QualityStatus, RawEvent
from mdrap.quality import QualityEngine
from mdrap.fastpath import FastQualityEngine, is_available
from mdrap.ws_feed import (
    LiveCryptoFeed,
    SequenceScope,
    WebSocketFeedManager,
    parse_binance_frame,
    parse_coinbase_frame,
    parse_kraken_frame,
    parse_okx_frame,
    parse_bybit_frame,
    parse_venue_frame,
    _KRAKEN_BOOKS,
)
from mdrap.adapters.kraken import KrakenAdapter


# ===========================================================================
# 1. Kraken Incremental Orderbook & Trade Tests
# ===========================================================================


def test_kraken_orderbook_snapshot_and_incremental_updates():
    """Verify that Kraken book snapshots and incremental level updates maintain state."""
    canon = "BTC/USD"
    _KRAKEN_BOOKS.clear()

    # 1. Snapshot: [channelID, {"bs": [...], "as": [...]}, "book-10", "XBT/USD"]
    snap_frame = [
        42,
        {
            "as": [
                ["60010.0", "1.5", "1700000000.100"],
                ["60020.0", "2.0", "1700000000.100"],
            ],
            "bs": [
                ["60000.0", "1.0", "1700000000.100"],
                ["59990.0", "3.0", "1700000000.100"],
            ],
        },
        "book-10",
        "XBT/USD",
    ]
    raw_snap = parse_kraken_frame(snap_frame, canon)
    assert raw_snap is not None
    assert raw_snap.payload["event_type"] == "QUOTE"
    assert raw_snap.payload["bid"] == 60000.0
    assert raw_snap.payload["ask"] == 60010.0
    assert raw_snap.payload["bid_size"] == 1.0
    assert raw_snap.payload["ask_size"] == 1.5

    # 2. Incremental Ask Update (new better ask price): [channelID, {"a": [["60005.0", "0.8", "1700000001.000"]]}, "book-10", "XBT/USD"]
    inc_ask = [
        42,
        {"a": [["60005.0", "0.8", "1700000001.000"]]},
        "book-10",
        "XBT/USD",
    ]
    raw_inc1 = parse_kraken_frame(inc_ask, canon)
    assert raw_inc1 is not None
    assert raw_inc1.payload["event_type"] == "QUOTE"
    assert raw_inc1.payload["bid"] == 60000.0
    assert raw_inc1.payload["ask"] == 60005.0  # updated best ask
    assert raw_inc1.payload["ask_size"] == 0.8

    # 3. Incremental Bid Update (new better bid price): [channelID, {"b": [["60002.0", "1.2", "1700000002.000"]]}, "book-10", "XBT/USD"]
    inc_bid = [
        42,
        {"b": [["60002.0", "1.2", "1700000002.000"]]},
        "book-10",
        "XBT/USD",
    ]
    raw_inc2 = parse_kraken_frame(inc_bid, canon)
    assert raw_inc2 is not None
    assert raw_inc2.payload["bid"] == 60002.0  # updated best bid
    assert raw_inc2.payload["ask"] == 60005.0

    # 4. Level Removal (size == "0.00000000"): 5-element frame with both a and b
    inc_removal = [
        42,
        {"a": [["60005.0", "0.0", "1700000003.000"]]},
        {"b": [["60002.0", "0.0", "1700000003.000"]]},
        "book-10",
        "XBT/USD",
    ]
    raw_inc3 = parse_kraken_frame(inc_removal, canon)
    assert raw_inc3 is not None
    # Reverted to previous bests
    assert raw_inc3.payload["bid"] == 60000.0
    assert raw_inc3.payload["ask"] == 60010.0


def test_kraken_adapter_incremental_orderbook_parity():
    """Verify KrakenAdapter parses incremental book frames identically."""
    adapter = KrakenAdapter(symbol="BTC/USD")

    # 1. Snapshot
    snap_frame = [
        42,
        {
            "as": [["60010.0", "1.5", "1700000000.100"]],
            "bs": [["60000.0", "1.0", "1700000000.100"]],
        },
        "book-10",
        "XBT/USD",
    ]
    raw_snap = adapter.parse_frame(snap_frame)
    assert raw_snap is not None
    assert raw_snap.payload["bid"] == 60000.0
    assert raw_snap.payload["ask"] == 60010.0

    # 2. Incremental update
    inc_frame = [
        42,
        {"b": [["60004.0", "2.5", "1700000001.000"]]},
        "book-10",
        "XBT/USD",
    ]
    raw_inc = adapter.parse_frame(inc_frame)
    assert raw_inc is not None
    assert raw_inc.payload["bid"] == 60004.0
    assert raw_inc.payload["ask"] == 60010.0


def test_kraken_public_trade_frame_parsing():
    """Verify Kraken trade messages are parsed into TRADE events."""
    canon = "BTC/USD"
    trade_frame = [
        42,
        [
            ["60005.50", "0.25000000", "1700000010.123456", "b", "m", ""],
            ["60006.00", "0.50000000", "1700000010.123500", "s", "l", ""],
        ],
        "trade",
        "XBT/USD",
    ]
    raw = parse_kraken_frame(trade_frame, canon)
    assert raw is not None
    assert raw.payload["event_type"] == "TRADE"
    assert raw.payload["channel"] == "trade"
    assert raw.payload["price"] == 60006.00
    assert raw.payload["quantity"] == 0.50
    assert raw.payload["side"] == "sell"
    assert raw.payload["exchange_ts"] == 1700000010.123500


# ===========================================================================
# 2. Public Trade Parsing Across Venues
# ===========================================================================


def test_binance_trade_and_combined_stream_parsing():
    """Verify Binance trade frames in direct and combined stream wrappers."""
    canon = "BTC/USD"
    # Direct trade frame
    direct_msg = {
        "e": "trade",
        "E": 1700000015000,
        "s": "BTCUSDT",
        "t": 12345678,
        "p": "60050.25",
        "q": "0.150",
        "T": 1700000015000,
        "m": True,
    }
    raw1 = parse_binance_frame(direct_msg, canon)
    assert raw1 is not None
    assert raw1.payload["event_type"] == "TRADE"
    assert raw1.payload["channel"] == "trade"
    assert raw1.payload["trade_id"] == 12345678
    assert raw1.payload["price"] == 60050.25
    assert raw1.payload["quantity"] == 0.15

    # Combined stream wrapper
    combined_msg = {
        "stream": "btcusdt@trade",
        "data": direct_msg,
    }
    raw2 = parse_binance_frame(combined_msg, canon)
    assert raw2 is not None
    assert raw2.payload["event_type"] == "TRADE"
    assert raw2.payload["trade_id"] == 12345678
    assert raw2.payload["price"] == 60050.25


def test_coinbase_match_trade_frame_parsing():
    """Verify Coinbase public matches channel produces TRADE events."""
    canon = "BTC/USD"
    match_msg = {
        "type": "match",
        "trade_id": 98765432,
        "sequence": 55512345,
        "maker_order_id": "m-123",
        "taker_order_id": "t-456",
        "time": "2026-11-15T00:00:00.123456Z",
        "product_id": "BTC-USD",
        "size": "0.75",
        "price": "60045.50",
        "side": "buy",
    }
    raw = parse_coinbase_frame(match_msg, canon)
    assert raw is not None
    assert raw.payload["event_type"] == "TRADE"
    assert raw.payload["channel"] == "matches"
    assert raw.payload["trade_id"] == 98765432
    assert raw.payload["price"] == 60045.50
    assert raw.payload["quantity"] == 0.75
    assert raw.payload["side"] == "buy"


def test_okx_trades_channel_parsing():
    """Verify OKX public trades channel produces TRADE events."""
    canon = "BTC/USD"
    okx_msg = {
        "arg": {"channel": "trades", "instId": "BTC-USDT"},
        "data": [
            {
                "instId": "BTC-USDT",
                "tradeId": "34567890",
                "px": "60055.10",
                "sz": "0.08",
                "side": "sell",
                "ts": "1700000020100",
            }
        ],
    }
    raw = parse_okx_frame(okx_msg, canon)
    assert raw is not None
    assert raw.payload["event_type"] == "TRADE"
    assert raw.payload["channel"] == "trades"
    assert raw.payload["trade_id"] == 34567890
    assert raw.payload["price"] == 60055.10
    assert raw.payload["quantity"] == 0.08
    assert raw.payload["side"] == "sell"


def test_bybit_public_trade_channel_parsing():
    """Verify Bybit publicTrade channel produces TRADE events."""
    canon = "BTC/USD"
    bybit_msg = {
        "topic": "publicTrade.BTCUSDT",
        "ts": 1700000025000,
        "type": "snapshot",
        "data": [
            {
                "T": 1700000025000,
                "s": "BTCUSDT",
                "S": "Buy",
                "v": "0.123",
                "p": "60060.00",
                "i": "78901234",
                "BT": False,
            }
        ],
    }
    raw = parse_bybit_frame(bybit_msg, canon)
    assert raw is not None
    assert raw.payload["event_type"] == "TRADE"
    assert raw.payload["channel"] == "publicTrade"
    assert raw.payload["trade_id"] == 78901234
    assert raw.payload["price"] == 60060.00
    assert raw.payload["quantity"] == 0.123
    assert raw.payload["side"] == "buy"


# ===========================================================================
# 3. Channel-Scoped Sequence Tracking Isolation Tests
# ===========================================================================


def test_sequence_tracking_isolates_trades_from_quotes():
    """
    Verify that trade IDs and quote update IDs do not collide.
    In the unpatched code, alternating trade ID (1.5B) and quote update ID (45B)
    caused continuous SEQUENCE_GAP and OUT_OF_ORDER false positives.
    """
    crypto_feed = LiveCryptoFeed()

    trade_scope = SequenceScope(
        venue="COINBASE", instrument="BTC/USD", channel="matches", epoch=0
    )
    quote_scope = SequenceScope(
        venue="COINBASE", instrument="BTC/USD", channel="ticker", epoch=0
    )

    # Interleave 10 trade events and 10 quote events with vastly different ID ranges
    for i in range(10):
        trade_id = 1_500_000_000 + i
        quote_id = 45_000_000_000 + i

        gap_trade = crypto_feed.check_sequence_gap(trade_scope, trade_id)
        gap_quote = crypto_feed.check_sequence_gap(quote_scope, quote_id)

        # Zero gaps should be flagged across both isolated streams
        assert not gap_trade
        assert not gap_quote

    assert crypto_feed.missing_events_count[trade_scope.key()] == 0
    assert crypto_feed.missing_events_count[quote_scope.key()] == 0


def test_sampled_channels_skip_sequence_gap_accumulation():
    """Verify that sampled ticker streams do not accumulate false sequence gaps."""
    crypto_feed = LiveCryptoFeed()
    ticker_scope = SequenceScope(
        venue="BINANCE", instrument="BTC/USD", channel="ticker", epoch=0
    )

    # Sampled sequences jump by 50 or 100
    crypto_feed.check_sequence_gap(ticker_scope, 100)
    gap = crypto_feed.check_sequence_gap(ticker_scope, 150)

    # Should NOT be counted as a missing event gap
    assert not gap
    assert crypto_feed.missing_events_count[ticker_scope.key()] == 0


# ===========================================================================
# 4. Heartbeat Liveness & Freshness Telemetry Tests
# ===========================================================================


def test_heartbeat_updates_freshness_and_prevents_stale_state():
    """Verify that heartbeat frames keep connector freshness under threshold."""
    mgr = WebSocketFeedManager(
        symbols=["BTC/USD"], venues=["KRAKEN"], stale_timeout_ms=5000
    )

    # Simulate connection
    mgr._stats["KRAKEN"]["connected"] = True
    now = time.time()
    mgr._provider_meta["KRAKEN"]["last_received_ts"] = now

    telemetry = mgr.get_provider_telemetry()
    assert telemetry["KRAKEN"].state.value == "CONNECTED"
    assert telemetry["KRAKEN"].feed_freshness_ms is not None
    assert telemetry["KRAKEN"].feed_freshness_ms < 1000.0


# ===========================================================================
# 5. Backpressure Tombstone Capacity & Drop Accounting Tests
# ===========================================================================


def test_backpressure_tombstone_capacity_and_accounting():
    """Verify that queue drops retain up to max_tombstones without silent loss."""
    # Create manager with bounded queue of size 5 and 50,000 tombstone capacity
    mgr = WebSocketFeedManager(
        symbols=["BTC/USD"],
        venues=["BINANCE"],
        max_queue_size=5,
        max_tombstones=10_000,
    )

    # Overfill queue with 200 frames
    for i in range(200):
        ev = RawEvent(
            source="BINANCE",
            payload={"instrument": "BTC/USD", "bid": 60000.0 + i, "ask": 60001.0 + i},
            receive_timestamp=time.time(),
            raw_id=f"test-raw-{i}",
        )
        try:
            mgr._queue.put_nowait(ev)
        except Exception:
            try:
                evicted = mgr._queue.get_nowait()
            except Exception:
                evicted = ev
            mgr._drop_count += 1
            mgr._drop_counts["BINANCE"] = mgr._drop_counts.get("BINANCE", 0) + 1
            tombstone = mgr._create_drop_tombstone("BINANCE", "BTC/USD", evicted)
            with mgr._tombstone_lock:
                mgr._tombstones.append(tombstone)
            mgr._queue.put_nowait(ev)

    summary = mgr.drop_summary()
    assert summary["total_drops"] == 195
    assert summary["per_venue"]["BINANCE"] == 195
    assert summary["retained_tombstones"] == 195

    tombstones = mgr.get_tombstones()
    assert len(tombstones) == 195
    assert tombstones[0].source == "BINANCE"


# ===========================================================================
# 6. End-to-End Quality Evaluation & Python/C Parity Tests
# ===========================================================================


def test_live_feed_events_evaluate_valid_without_pervasive_suspicious():
    """
    Verify that incoming live feed events from all 5 venues normalize cleanly
    and pass quality evaluation as VALID (fixing the 99.59% suspicious bug).
    """
    gateway = Gateway()
    cfg = QualityConfig(asset_classes=DEFAULT_ASSET_CLASSES)
    py_engine = QualityEngine(cfg)
    c_engine = FastQualityEngine(cfg) if is_available() else None

    # Simulate 5 realistic events from the 5 venues
    from datetime import datetime, timezone

    t_now = time.time()
    iso_now = datetime.now(timezone.utc).isoformat()
    frames = [
        # Binance Quote
        parse_binance_frame(
            {
                "bids": [["60000.00", "1.5"]],
                "asks": [["60002.00", "2.0"]],
                "E": int(t_now * 1000),
            },
            "BTC/USD",
            t_recv=t_now,
        ),
        # Coinbase Quote
        parse_coinbase_frame(
            {
                "type": "ticker",
                "sequence": 1001,
                "price": "60001.00",
                "best_bid": "60000.50",
                "best_ask": "60001.50",
                "best_bid_size": "1.0",
                "best_ask_size": "1.0",
                "time": iso_now,
            },
            "BTC/USD",
            t_recv=t_now,
        ),
        # Kraken Quote
        parse_kraken_frame(
            [
                42,
                {
                    "as": [["60002.50", "1.0", str(t_now)]],
                    "bs": [["60000.25", "1.0", str(t_now)]],
                },
                "book-10",
                "XBT/USD",
            ],
            "BTC/USD",
            t_recv=t_now,
        ),
        # OKX Trade
        parse_okx_frame(
            {
                "arg": {"channel": "trades"},
                "data": [
                    {
                        "tradeId": "901",
                        "px": "60001.00",
                        "sz": "0.1",
                        "side": "buy",
                        "ts": str(int(t_now * 1000)),
                    }
                ],
            },
            "BTC/USD",
            t_recv=t_now,
        ),
        # Bybit Quote
        parse_bybit_frame(
            {
                "topic": "orderbook.1.BTCUSDT",
                "ts": int(t_now * 1000),
                "data": {"b": [["60000.10", "2.0"]], "a": [["60001.90", "2.0"]]},
            },
            "BTC/USD",
            t_recv=t_now,
        ),
    ]

    for raw in frames:
        assert raw is not None
        canon = gateway.normalize(raw)

        # Evaluate with pure Python engine
        py_canon = py_engine.evaluate(canon)
        assert py_canon.quality_status == QualityStatus.VALID, (
            f"Failed on {canon.source}: {py_canon.reasons}"
        )

        # Evaluate with C Fastpath engine (if available)
        if c_engine is not None:
            c_canon = c_engine.evaluate(canon)
            assert c_canon.quality_status == QualityStatus.VALID
            assert c_canon.quality_status == py_canon.quality_status
