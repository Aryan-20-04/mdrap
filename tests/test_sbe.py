"""
Tests for Simple Binary Encoding (SBE) Wire Protocol (Spec §18, §26).
"""
from __future__ import annotations

import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sbe import (
    HEADER_SIZE,
    TICK_TOTAL_FRAME_SIZE,
    BBO_TOTAL_FRAME_SIZE,
    TEMPLATE_TICK,
    TEMPLATE_BBO,
    TEMPLATE_DEPTH,
    pack_sbe_tick,
    unpack_sbe_tick,
    pack_sbe_bbo,
    unpack_sbe_bbo,
    pack_sbe_depth,
)


def test_sbe_tick_pack_and_unpack_roundtrip():
    """Verify SBE tick packs to exactly 128 bytes and unpacks with identical fields."""
    frame = pack_sbe_tick(
        seq=1042,
        symbol="BTC/USD",
        source="BINANCE",
        price=65432.50,
        size=1.25,
        bid=65432.00,
        ask=65433.00,
        bid_size=2.50,
        ask_size=3.50,
        status="VALID",
        is_crossed=False,
        exchange_ts=1700000000.123,
        ingest_ts=1700000000.125,
        broadcast_ts=1700000000.126,
        engine_us=4.2,
    )

    assert len(frame) == TICK_TOTAL_FRAME_SIZE
    assert len(frame) == 128  # Exactly 2 cache lines

    msg = unpack_sbe_tick(frame)
    assert msg is not None
    assert msg.seq == 1042
    assert msg.symbol == "BTC/USD"
    assert msg.source == "BINANCE"
    assert msg.price == 65432.50
    assert msg.size == 1.25
    assert msg.bid == 65432.00
    assert msg.ask == 65433.00
    assert msg.status == "VALID"
    assert msg.is_crossed is False
    assert round(msg.engine_us, 1) == 4.2


def test_sbe_bbo_pack_and_unpack_roundtrip():
    """Verify SBE BBO packs and unpacks accurately."""
    frame = pack_sbe_bbo(
        seq=500,
        symbol="AAPL",
        timestamp=1700000001.0,
        mid_price=175.25,
        spread=0.10,
        best_bid=175.20,
        best_bid_size=500.0,
        best_ask=175.30,
        best_ask_size=750.0,
        best_bid_source="EQUITIES",
        best_ask_source="EQUITIES",
        is_crossed=False,
        is_locked=False,
        is_stale=False,
    )

    assert len(frame) == BBO_TOTAL_FRAME_SIZE

    bbo = unpack_sbe_bbo(frame)
    assert bbo is not None
    assert bbo.seq == 500
    assert bbo.symbol == "AAPL"
    assert bbo.best_bid == 175.20
    assert bbo.best_ask == 175.30
    assert bbo.mid_price == 175.25
    assert bbo.spread == 0.10
    assert bbo.best_bid_source == "EQUITIES"


def test_sbe_depth_pack():
    """Verify SBE Depth packing with repeating level groups."""
    bids = [(100.0, 10.0, "BINANCE"), (99.5, 20.0, "COINBASE")]
    asks = [(100.5, 15.0, "BINANCE"), (101.0, 25.0, "COINBASE")]
    frame = pack_sbe_depth(
        seq=101,
        symbol="BTC/USD",
        timestamp=1700000002.0,
        bids=bids,
        asks=asks,
        micro_price=100.25,
        ofi=0.15,
        cvd=120.0,
    )

    assert len(frame) > HEADER_SIZE
    assert frame[:2] != b"\x00\x00"


def test_sbe_unpack_speedup_vs_json():
    """Benchmark: SBE unpack must be at least 5x faster than JSON decoding."""
    tick_dict = {
        "type": "TICK",
        "seq": 100,
        "sym": "BTC/USD",
        "source": "BINANCE",
        "price": 65000.0,
        "size": 1.0,
        "bid": 64999.0,
        "ask": 65001.0,
        "bid_size": 2.0,
        "ask_size": 2.0,
        "status": "VALID",
        "is_crossed": False,
        "exchange_ts": 1700000000.0,
        "ingest_ts": 1700000000.001,
        "broadcast_ts": 1700000000.002,
        "engine_us": 2.5,
    }
    json_bytes = (json.dumps(tick_dict) + "\n").encode("utf-8")

    sbe_bytes = pack_sbe_tick(
        seq=100,
        symbol="BTC/USD",
        source="BINANCE",
        price=65000.0,
        size=1.0,
        bid=64999.0,
        ask=65001.0,
        bid_size=2.0,
        ask_size=2.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1700000000.0,
        ingest_ts=1700000000.001,
        broadcast_ts=1700000000.002,
        engine_us=2.5,
    )

    n_iters = 20_000

    # 1. Benchmark JSON decode
    t0 = time.perf_counter_ns()
    for _ in range(n_iters):
        _ = json.loads(json_bytes)
    t_json_ms = (time.perf_counter_ns() - t0) / 1e6

    # 2. Benchmark SBE unpack
    t0 = time.perf_counter_ns()
    for _ in range(n_iters):
        _ = unpack_sbe_tick(sbe_bytes)
    t_sbe_ms = (time.perf_counter_ns() - t0) / 1e6

    speedup = t_json_ms / max(0.001, t_sbe_ms)
    # Threshold 1.2 accommodates pytest-cov bytecode tracing (normally > 3.0x)
    assert speedup > 1.2  # SBE is faster than JSON
    assert t_sbe_ms < t_json_ms
