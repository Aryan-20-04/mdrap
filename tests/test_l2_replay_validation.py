"""
MDRAP 6.0.4 — Deterministic Unit Tests for Sub-Second L2 Book Reconstruction,
Replay Integrity, Strict Aggressor-Side Matching, Multi-Level Sweeps, and
L2 vs L3 Queue-Priority Labeling.
"""

from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.join(_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tools"))

from mdrap_strategies.matching_engine import (
    FeeSchedule,
    MatchingEngineConfig,
    RealisticMatchingEngine,
    SimOrderSide,
    SimOrderStatus,
    SimOrderType,
)
from replay_l2 import (
    L2BookReconstructor,
    compute_kraken_crc32,
    compute_okx_crc32,
    run_l2_replay_pass,
)


def test_require_aggressor_side_blocks_silent_price_only_matching():
    """With require_aggressor_side=True, trades without aggressor_side must never fill resting orders."""
    engine = RealisticMatchingEngine(
        MatchingEngineConfig(
            network_latency_ms=1.0,
            matching_delay_ms=0.5,
            require_aggressor_side=True,
            queue_priority_model="L2_ESTIMATED",
        )
    )
    engine.update_book("BTC/USD", [(62000.0, 0.0)], [(62001.0, 1.0)], timestamp=100.0)
    engine.submit_order(
        "bid-1", "BTC/USD", SimOrderSide.BUY, SimOrderType.LIMIT,
        quantity=0.5, price=62000.0, current_time=100.0, post_only=True,
    )
    engine.process_time_slice(100.005)
    assert engine.orders["bid-1"].status == SimOrderStatus.ACCEPTED

    # Trade at 62000.0 with aggressor_side=None must NOT silently match
    fills = engine.process_market_trade(
        "BTC/USD", trade_price=62000.0, trade_size=5.0, timestamp=100.010, aggressor_side=None
    )
    assert fills == []
    assert engine.orders["bid-1"].filled_quantity == 0.0
    assert engine.skipped_no_aggressor_trades == 1

    # Explicit SELL aggressor hits resting BUY bid and tags L2_ESTIMATED
    fills2 = engine.process_market_trade(
        "BTC/USD", trade_price=62000.0, trade_size=0.5, timestamp=100.020, aggressor_side=SimOrderSide.SELL
    )
    assert len(fills2) == 1
    assert fills2[0]["is_maker"] is True
    assert fills2[0]["queue_priority_model"] == "L2_ESTIMATED"
    assert engine.orders["bid-1"].status == SimOrderStatus.FILLED


def test_multi_level_sweep_walks_depth_and_computes_exact_vwap():
    """Aggressive market order must walk multiple L2 ask levels and deplete book depth accurately."""
    engine = RealisticMatchingEngine(
        MatchingEngineConfig(
            network_latency_ms=0.0,
            matching_delay_ms=0.0,
            fee_schedule=FeeSchedule(maker_fee_bps=1.0, taker_fee_bps=5.0),
            require_aggressor_side=True,
        )
    )
    asks = [(100.0, 1.0), (100.5, 2.0), (101.0, 3.0)]
    bids = [(99.5, 5.0)]
    engine.update_book("ETH/USD", bids, asks, timestamp=1.0)
    engine.submit_order("sw-1", "ETH/USD", SimOrderSide.BUY, SimOrderType.MARKET, quantity=4.0, price=None, current_time=1.0)
    events = engine.process_time_slice(1.001)
    fills = [e for e in events if e.get("event") == "FILL"]

    assert len(fills) == 3
    assert [(f["price"], f["quantity"]) for f in fills] == [(100.0, 1.0), (100.5, 2.0), (101.0, 1.0)]
    assert all(f["queue_priority_model"] == "TAKER_BOOK_WALK" for f in fills)
    expected_vwap = (100.0 * 1.0 + 100.5 * 2.0 + 101.0 * 1.0) / 4.0
    assert engine.orders["sw-1"].avg_fill_price == pytest.approx(expected_vwap)
    assert engine.orders["sw-1"].status == SimOrderStatus.FILLED
    # Top two ask levels fully depleted; 2.0 remains at 101.0
    assert 100.0 not in engine._market_asks["ETH/USD"]
    assert 100.5 not in engine._market_asks["ETH/USD"]
    assert engine._market_asks["ETH/USD"][101.0] == pytest.approx(2.0)


def test_kraken_and_okx_crc32_checksum_verification():
    """Verify Kraken and OKX CRC32 algorithms accept valid books and detect corruption."""
    # Kraken book-10 snapshot + delta with exact CRC32
    asks_raw = {60001.0: ("60001.00000", "1.50000000")}
    bids_raw = {60000.0: ("60000.00000", "2.00000000")}
    expected_k = compute_kraken_crc32(asks_raw, bids_raw, depth=10)

    rec = L2BookReconstructor()
    rec.process_frame({
        "venue": "KRAKEN", "instrument": "BTC/USD", "channel": "depth", "epoch": 0,
        "recv_mono_ns": 1000, "recv_wall_s": 1700000000.0,
        "wire": [1, {"as": [["60001.00000", "1.00000000", "1700000000.0"]], "bs": [["60000.00000", "2.00000000", "1700000000.0"]]}, "book-10", "XBT/USD"],
    })
    evs_ok = rec.process_frame({
        "venue": "KRAKEN", "instrument": "BTC/USD", "channel": "depth", "epoch": 0,
        "recv_mono_ns": 2000, "recv_wall_s": 1700000000.1,
        "wire": [1, {"a": [["60001.00000", "1.50000000", "1700000000.1"]], "c": str(expected_k)}, "book-10", "XBT/USD"],
    })
    assert evs_ok[0].checksum_ok is True

    evs_bad = rec.process_frame({
        "venue": "KRAKEN", "instrument": "BTC/USD", "channel": "depth", "epoch": 0,
        "recv_mono_ns": 3000, "recv_wall_s": 1700000000.2,
        "wire": [1, {"a": [["60001.00000", "1.50000000", "1700000000.2"]], "c": str(expected_k ^ 1)}, "book-10", "XBT/USD"],
    })
    assert evs_bad[0].checksum_ok is False

    # OKX signed 32-bit CRC32 + prevSeqId gap detection
    okx_bids = {3000.0: ("3000", "4.0")}
    okx_asks = {3000.5: ("3000.5", "3.0")}
    expected_o = compute_okx_crc32(okx_bids, okx_asks, depth=25)
    evs_snap = rec.process_frame({
        "venue": "OKX", "instrument": "ETH/USD", "channel": "depth", "epoch": 0,
        "recv_mono_ns": 4000, "recv_wall_s": 1700000000.0,
        "wire": {
            "arg": {"channel": "books", "instId": "ETH-USDT"}, "action": "snapshot",
            "data": [{"bids": [["3000", "4.0", "0", "1"]], "asks": [["3000.5", "3.0", "0", "1"]],
                      "ts": "1700000000000", "checksum": expected_o, "seqId": 100, "prevSeqId": -1}],
        },
    })
    assert evs_snap[0].checksum_ok is True
    # Inject prevSeqId gap (expects 100, gets 102)
    evs_gap = rec.process_frame({
        "venue": "OKX", "instrument": "ETH/USD", "channel": "depth", "epoch": 0,
        "recv_mono_ns": 5000, "recv_wall_s": 1700000000.1,
        "wire": {
            "arg": {"channel": "books", "instId": "ETH-USDT"}, "action": "update",
            "data": [{"bids": [], "asks": [], "ts": "1700000000100", "checksum": expected_o, "seqId": 105, "prevSeqId": 102}],
        },
    })
    assert evs_gap[0].sequence_gap is True


def test_binance_buffer_sync_coinbase_aggressor_and_replay_determinism():
    """Verify Binance snapshot+buffer sync, Coinbase maker-side inversion, and bit-exact replay determinism."""
    frames = [
        # Buffered Binance delta arriving before REST snapshot
        {"venue": "BINANCE", "instrument": "BTC/USD", "channel": "depth", "epoch": 0,
         "recv_mono_ns": 1000, "recv_wall_s": 1700000000.01,
         "wire": {"e": "depthUpdate", "E": 1700000000010, "s": "BTCUSDT", "U": 501, "u": 502, "b": [["62000.0", "2.0"]], "a": []}},
        # REST snapshot with lastUpdateId=500 -> buffered delta U=501..502 applies cleanly
        {"venue": "BINANCE", "instrument": "BTC/USD", "channel": "depth_snapshot", "epoch": 0,
         "recv_mono_ns": 2000, "recv_wall_s": 1700000000.02,
         "wire": {"lastUpdateId": 500, "bids": [["62000.0", "1.0"]], "asks": [["62001.0", "1.0"]]}},
        # Next delta with gap (U=505 instead of 503)
        {"venue": "BINANCE", "instrument": "BTC/USD", "channel": "depth", "epoch": 0,
         "recv_mono_ns": 3000, "recv_wall_s": 1700000000.12,
         "wire": {"e": "depthUpdate", "E": 1700000000120, "s": "BTCUSDT", "U": 505, "u": 506, "b": [["62000.0", "2.5"]], "a": []}},
        # Coinbase snapshot + match (maker side='sell' => aggressive BUY)
        {"venue": "COINBASE", "instrument": "BTC/USD", "channel": "depth", "epoch": 0,
         "recv_mono_ns": 4000, "recv_wall_s": 1700000000.15,
         "wire": {"type": "snapshot", "product_id": "BTC-USD", "bids": [["62000.0", "1.0"]], "asks": [["62001.0", "1.0"]]}},
        {"venue": "COINBASE", "instrument": "BTC/USD", "channel": "trade", "epoch": 0,
         "recv_mono_ns": 5000, "recv_wall_s": 1700000000.20,
         "wire": {"type": "match", "trade_id": 999, "sequence": 1001, "product_id": "BTC-USD",
                  "price": "62001.0", "size": "0.25", "side": "sell", "time": "2023-11-14T22:13:20.200000Z"}},
    ]

    p1 = run_l2_replay_pass(frames, check_py_c_parity=True, run_execution_probes=True)
    p2 = run_l2_replay_pass(frames, check_py_c_parity=False, run_execution_probes=True)

    assert p1["determinism_sha256"] == p2["determinism_sha256"]
    assert p1["parity_mismatches"] == 0
    assert p1["book_reconstruction"]["BINANCE:BTC/USD"]["deltas_buffered"] == 1
    assert p1["book_reconstruction"]["BINANCE:BTC/USD"]["sequence_gaps"] == 1
    # Coinbase match with maker side='sell' mapped to aggressive BUY and aligned with ask=62001.0
    assert p1["aggressor_alignment"]["COINBASE"]["buy"] == 1
    assert p1["aggressor_alignment"]["COINBASE"]["aligned_with_bbo"] == 1
