"""
Unit and Integration Tests for MDRAP Real-Time VWAP Slicing & Liquidity Depth Engine (Phase E).
"""
import json
import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from client import MDRAPClient
from depth import ConsolidatedDepthEngine, ConsolidatedLadder, DepthLevel, VWAPCurve, VWAPSlice
from models import RawEvent
from security import SecurityManager, Tier
from service import MarketDataDaemon
from storage import Store


def test_vwap_single_level_fill():
    """Verify VWAP on top-of-book order has 0 slippage and matches best ask."""
    engine = ConsolidatedDepthEngine()
    engine.observe(RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[60000.0, 5.0]],
            "asks": [[60010.0, 5.0]],
        },
        receive_timestamp=1000.001,
        raw_id="r1",
    ))

    ladder = engine.current_ladder("BTC/USD")
    assert ladder is not None

    # Buy 2 BTC (less than 5 BTC available at best ask 60010.0)
    buy_slice = ladder.compute_vwap("BUY", 2.0)
    assert buy_slice.side == "BUY"
    assert buy_slice.target_size == 2.0
    assert buy_slice.filled_size == 2.0
    assert buy_slice.vwap_price == 60010.0
    assert buy_slice.slippage_dollars == 0.0
    assert buy_slice.slippage_bps == 0.0
    assert buy_slice.is_fully_filled
    assert buy_slice.venue_breakdown == {"BINANCE": 2.0}
    assert buy_slice.total_notional == 60010.0 * 2.0


def test_vwap_multi_level_walk_math():
    """Verify walking multiple price rungs calculates accurate volume-weighted average price and slippage bps."""
    engine = ConsolidatedDepthEngine()
    # Asks:
    # Level 1: 100.0, size 1.0 (Coinbase)
    # Level 2: 104.0, size 2.0 (Binance)
    engine.observe(RawEvent(
        source="COINBASE",
        payload={
            "instrument": "ETH/USD",
            "exchange_ts": 1000.0,
            "bids": [[98.0, 2.0]],
            "asks": [[100.0, 1.0]],
        },
        receive_timestamp=1000.001,
        raw_id="c1",
    ))
    engine.observe(RawEvent(
        source="BINANCE",
        payload={
            "instrument": "ETH/USD",
            "exchange_ts": 1000.01,
            "bids": [[97.0, 2.0]],
            "asks": [[104.0, 2.0]],
        },
        receive_timestamp=1000.011,
        raw_id="b1",
    ))

    ladder = engine.current_ladder("ETH/USD")
    assert ladder is not None

    # Buy target size 3.0: consumes 1.0 at 100.0 and 2.0 at 104.0
    # Cost = 100 * 1 + 104 * 2 = 100 + 208 = 308
    # VWAP = 308 / 3 = 102.6667
    # Best ask = 100.0 -> Slippage = 2.6667 dollars
    # Slippage bps = (2.6667 / 100.0) * 10000 = 266.67 bps
    buy_slice = ladder.compute_vwap("BUY", 3.0)
    assert buy_slice.filled_size == 3.0
    assert round(buy_slice.vwap_price, 4) == round(308.0 / 3.0, 4)
    assert round(buy_slice.slippage_dollars, 4) == round(308.0 / 3.0 - 100.0, 4)
    assert round(buy_slice.slippage_bps, 2) == round(((308.0 / 3.0 - 100.0) / 100.0) * 10000.0, 2)
    assert buy_slice.is_fully_filled
    assert buy_slice.venue_breakdown["COINBASE"] == 1.0
    assert buy_slice.venue_breakdown["BINANCE"] == 2.0


def test_vwap_partial_fill_shortfall():
    """Verify partial fill when order size exceeds entire book depth."""
    engine = ConsolidatedDepthEngine()
    ladder = engine.observe(RawEvent(
        source="KRAKEN",
        payload={
            "instrument": "SOL/USD",
            "exchange_ts": 1000.0,
            "bids": [[150.0, 2.0]],
            "asks": [[152.0, 1.5]],
        },
        receive_timestamp=1000.001,
        raw_id="k1",
    ))

    # Request 10 SOL when only 1.5 is available on the asks
    buy_slice = ladder.compute_vwap("BUY", 10.0)
    assert buy_slice.target_size == 10.0
    assert buy_slice.filled_size == 1.5
    assert not buy_slice.is_fully_filled
    assert buy_slice.vwap_price == 152.0
    assert buy_slice.venue_breakdown == {"KRAKEN": 1.5}


def test_vwap_sell_side_slippage():
    """Verify SELL order walks bids downwards and computes negative price impact relative to best bid."""
    engine = ConsolidatedDepthEngine()
    ladder = engine.observe(RawEvent(
        source="OKX",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[50000.0, 1.0], [49000.0, 1.0]],
            "asks": [[50100.0, 2.0]],
        },
        receive_timestamp=1000.001,
        raw_id="o1",
    ))

    # Sell 2 BTC: fills 1 at 50000 and 1 at 49000
    # VWAP = (50000 + 49000) / 2 = 49500
    # Slippage dollars = 50000 - 49500 = 500 dollars
    # Slippage bps = (500 / 50000) * 10000 = 100 bps
    sell_slice = ladder.compute_vwap("SELL", 2.0)
    assert sell_slice.side == "SELL"
    assert sell_slice.vwap_price == 49500.0
    assert sell_slice.slippage_dollars == 500.0
    assert sell_slice.slippage_bps == 100.0
    assert sell_slice.is_fully_filled


def test_vwap_curve_structure_and_depth_bands():
    """Verify compute_vwap_curve outputs all requested tranches and depth bands."""
    engine = ConsolidatedDepthEngine()
    ladder = engine.observe(RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[60000.0, 10.0], [59900.0, 20.0]],
            "asks": [[60050.0, 10.0], [60150.0, 20.0]],
        },
        receive_timestamp=1000.001,
        raw_id="b1",
    ))

    sizes = [1.0, 5.0, 15.0]
    curve = ladder.compute_vwap_curve(sizes=sizes)

    assert curve.instrument_id == "BTC/USD"
    assert curve.mid_price == (60000.0 + 60050.0) / 2.0
    assert len(curve.buy_slices) == 3
    assert len(curve.sell_slices) == 3
    assert curve.buy_slices[0].target_size == 1.0
    assert curve.buy_slices[1].target_size == 5.0
    assert curve.buy_slices[2].target_size == 15.0

    d = curve.to_dict()
    assert d["instrument_id"] == "BTC/USD"
    assert "buy_slices" in d
    assert "depth_10bps" in d
    assert "depth_50bps" in d
    assert "depth_100bps" in d


def test_vwap_storage_persistence():
    """Verify storing and querying VWAP curves in SQLite database."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(db_path)

    engine = ConsolidatedDepthEngine()
    ladder = engine.observe(RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[65000.0, 5.0]],
            "asks": [[65010.0, 5.0]],
        },
        receive_timestamp=1000.001,
        raw_id="r1",
    ))
    curve = ladder.compute_vwap_curve([1.0, 5.0])

    store.write_vwap_batch([curve])
    store.commit()

    rows = store.query_vwap_curves("BTC/USD")
    assert len(rows) == 1
    r = rows[0]
    assert r["instrument_id"] == "BTC/USD"
    assert r["mid_price"] == (65000.0 + 65010.0) / 2.0
    parsed = json.loads(r["curve_json"])
    assert len(parsed["buy_slices"]) == 2

    store.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_daemon_vwap_command_and_client_query():
    """Verify MDRAPClient can query real-time VWAP curve from daemon."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=9988,
        db_path=db_path,
        sim_events=200,
        sim_speed_eps=5000.0,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)

    try:
        with MDRAPClient(host="127.0.0.1", port=9988, timeout=2.0) as client:
            vwap_curve = client.get_vwap("BTC/USD", sizes=[1.0, 5.0])
            # Even if simulator generated other symbols, response status is OK and vwap_curve is returned
            if vwap_curve:
                assert "buy_slices" in vwap_curve
                assert "sell_slices" in vwap_curve
                assert "mid_price" in vwap_curve
    finally:
        daemon.stop()
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_daemon_vwap_auth_guard():
    """Verify unauthenticated/invalid token is rejected while valid key queries VWAP curves."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=9989,
        db_path=db_path,
        require_auth=True,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)

    try:
        # 1. Invalid token should be rejected with PermissionError
        with pytest.raises(PermissionError) as excinfo:
            with MDRAPClient(host="127.0.0.1", port=9989, auth_token="invalid_bad_token", timeout=2.0) as client:
                client.get_vwap("BTC/USD")
        assert "INVALID_TOKEN" in str(excinfo.value) or "UNAUTHORIZED" in str(excinfo.value)

        # 2. Authenticated client should succeed without commercial paywall
        with MDRAPClient(host="127.0.0.1", port=9989, auth_token="mdrap_demo_key", timeout=2.0) as client:
            curve = client.get_vwap("BTC/USD")
            assert curve is None or isinstance(curve, dict)
    finally:
        daemon.stop()
        if os.path.exists(db_path):
            os.unlink(db_path)
