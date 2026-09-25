"""
Unit and Integration Tests for MDRAP Consolidated Level-2 Market Depth Engine.
"""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from depth import DepthLevel, ConsolidatedLadder, ConsolidatedDepthEngine
from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from storage import Store


def test_depth_level_dataclass():
    lvl = DepthLevel(price=65000.0, size=1.5, venue="BINANCE")
    assert lvl.price == 65000.0
    assert lvl.size == 1.5
    assert lvl.venue == "BINANCE"
    d = lvl.to_dict()
    assert d["price"] == 65000.0
    assert d["venue"] == "BINANCE"


def test_consolidated_depth_merging_and_sorting():
    """Verify bids are sorted descending (highest first) and asks ascending across venues."""
    engine = ConsolidatedDepthEngine()

    # Venue 1: Binance quotes bids at 65000 and 64990, asks at 65010 and 65020
    raw_binance = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "QUOTE",
            "exchange_ts": 1000.0,
            "bids": [[65000.0, 1.0], [64990.0, 2.0]],
            "asks": [[65010.0, 1.5], [65020.0, 3.0]],
        },
        receive_timestamp=1000.001,
        raw_id="b1",
    )
    engine.observe(raw_binance)

    # Venue 2: Coinbase quotes tighter bids at 65002 and tighter asks at 65008
    raw_coinbase = RawEvent(
        source="COINBASE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "QUOTE",
            "exchange_ts": 1000.05,
            "bids": [[65002.0, 0.5], [64995.0, 1.2]],
            "asks": [[65008.0, 0.8], [65015.0, 2.0]],
        },
        receive_timestamp=1000.051,
        raw_id="c1",
    )
    ladder = engine.observe(raw_coinbase)

    assert ladder is not None
    assert ladder.instrument_id == "BTC/USD"
    # Bids sorted descending: 65002 (Coinbase), 65000 (Binance), 64995 (Coinbase), 64990 (Binance)
    assert len(ladder.bids) == 4
    assert ladder.bids[0].price == 65002.0
    assert ladder.bids[0].venue == "COINBASE"
    assert ladder.bids[1].price == 65000.0
    assert ladder.bids[1].venue == "BINANCE"

    # Asks sorted ascending: 65008 (Coinbase), 65010 (Binance), 65015 (Coinbase), 65020 (Binance)
    assert len(ladder.asks) == 4
    assert ladder.asks[0].price == 65008.0
    assert ladder.asks[0].venue == "COINBASE"
    assert ladder.asks[1].price == 65010.0
    assert ladder.asks[1].venue == "BINANCE"

    # Not crossed: best bid 65002 < best ask 65008
    assert not ladder.is_crossed


def test_micro_price_and_order_imbalance():
    """Verify Volume-Weighted Micro-Price and Order Flow Imbalance (OFI)."""
    engine = ConsolidatedDepthEngine()

    # Best bid: 100.0 with size 3.0 | Best ask: 102.0 with size 1.0
    # Micro-price = (100 * 1.0 + 102 * 3.0) / (3.0 + 1.0) = (100 + 306) / 4 = 406 / 4 = 101.5
    raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "AAPL",
            "exchange_ts": 1000.0,
            "bids": [[100.0, 3.0]],
            "asks": [[102.0, 1.0]],
        },
        receive_timestamp=1000.001,
        raw_id="r1",
    )
    ladder = engine.observe(raw)
    assert ladder is not None
    assert round(ladder.micro_price, 2) == 101.50
    # Imbalance = (3.0 - 1.0) / (3.0 + 1.0) = +0.50 (Bid heavy)
    assert round(ladder.imbalance_ratio, 2) == 0.50


def test_cross_exchange_depth_arbitrage_detection():
    """Detect when a bid on Venue A exceeds an ask on Venue B."""
    engine = ConsolidatedDepthEngine()

    # Kraken offers cheaply at 64995
    raw_kraken = RawEvent(
        source="KRAKEN",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[64990.0, 1.0]],
            "asks": [[64995.0, 2.0]],
        },
        receive_timestamp=1000.001,
        raw_id="k1",
    )
    engine.observe(raw_kraken)

    # OKX bids aggressively at 65000 (> 64995 Kraken ask)
    raw_okx = RawEvent(
        source="OKX",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.01,
            "bids": [[65000.0, 1.5]],
            "asks": [[65008.0, 1.0]],
        },
        receive_timestamp=1000.011,
        raw_id="o1",
    )
    ladder = engine.observe(raw_okx)

    assert ladder.is_crossed
    assert len(ladder.crossed_opportunities) >= 1
    opp = ladder.crossed_opportunities[0]
    assert opp["bid_venue"] == "OKX"
    assert opp["bid_price"] == 65000.0
    assert opp["ask_venue"] == "KRAKEN"
    assert opp["ask_price"] == 64995.0
    assert opp["arb_spread"] == 5.0
    assert opp["max_volume"] == 1.5


def test_depth_ttl_eviction():
    """Verify expired venue depth books are pruned from global ladder."""
    engine = ConsolidatedDepthEngine(depth_ttl_s=1.0)

    # Venue A quotes at t=1000.0
    r1 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "ETH/USD",
            "exchange_ts": 1000.0,
            "bids": [[3500.0, 2.0]],
            "asks": [[3505.0, 2.0]],
        },
        receive_timestamp=1000.001,
        raw_id="r1",
    )
    engine.observe(r1)

    # Venue B quotes at t=1002.5 (>1.0s TTL later)
    r2 = RawEvent(
        source="COINBASE",
        payload={
            "instrument": "ETH/USD",
            "exchange_ts": 1002.5,
            "bids": [[3490.0, 1.0]],
            "asks": [[3495.0, 1.0]],
        },
        receive_timestamp=1002.501,
        raw_id="r2",
    )
    ladder = engine.observe(r2)

    # Binance should be evicted due to staleness; only Coinbase remains
    assert len(ladder.bids) == 1
    assert ladder.bids[0].venue == "COINBASE"
    assert ladder.bids[0].price == 3490.0


def test_depth_storage_persistence_and_query():
    """Verify writing and querying consolidated depth snapshots in SQLite Store."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(db_path)

    engine = ConsolidatedDepthEngine()
    r = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "SOL/USD",
            "exchange_ts": 1000.0,
            "bids": [[140.0, 10.0], [139.5, 20.0]],
            "asks": [[140.5, 8.0], [141.0, 15.0]],
        },
        receive_timestamp=1000.001,
        raw_id="r1",
    )
    ladder = engine.observe(r)

    # Persist
    store.write_depth_batch([ladder])
    store.commit()

    # Query back
    rows = store.query_depth("SOL/USD")
    assert len(rows) == 1
    row = rows[0]
    assert row["instrument_id"] == "SOL/USD"
    assert row["micro_price"] > 0
    assert "140.0" in row["bids_json"]
    assert "140.5" in row["asks_json"]

    store.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_price_level_aggregation_across_venues():
    """Verify multiple venues quoting the same price are coalesced into a single AggregatedLevel."""
    engine = ConsolidatedDepthEngine()

    # Binance quotes bid 50000 with size 2.0, ask 50010 with size 1.0
    engine.observe(
        RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC/USD",
                "exchange_ts": 1000.0,
                "bids": [[50000.0, 2.0], [49990.0, 5.0]],
                "asks": [[50010.0, 1.0], [50020.0, 4.0]],
            },
            receive_timestamp=1000.001,
            raw_id="b1",
        )
    )

    # Coinbase also quotes bid at the EXACT SAME price 50000 with size 3.5, and ask at 50010 with size 2.5
    ladder = engine.observe(
        RawEvent(
            source="COINBASE",
            payload={
                "instrument": "BTC/USD",
                "exchange_ts": 1000.01,
                "bids": [[50000.0, 3.5], [49980.0, 1.0]],
                "asks": [[50010.0, 2.5], [50030.0, 3.0]],
            },
            receive_timestamp=1000.011,
            raw_id="c1",
        )
    )

    assert ladder is not None
    # 1. Raw depth has 4 bids and 4 asks
    assert len(ladder.bids) == 4
    assert len(ladder.asks) == 4

    # 2. Aggregated bids has 3 distinct price rungs: 50000.0, 49990.0, 49980.0
    assert len(ladder.aggregated_bids) == 3
    top_bid = ladder.aggregated_bids[0]
    assert top_bid.price == 50000.0
    assert round(top_bid.total_size, 4) == 5.5  # 2.0 + 3.5
    assert top_bid.venue_sizes["BINANCE"] == 2.0
    assert top_bid.venue_sizes["COINBASE"] == 3.5
    assert top_bid.order_count == 2
    assert round(top_bid.cumulative_size, 4) == 5.5
    assert round(top_bid.cumulative_notional, 2) == 50000.0 * 5.5

    # 3. Aggregated asks has 3 distinct price rungs: 50010.0, 50020.0, 50030.0
    assert len(ladder.aggregated_asks) == 3
    top_ask = ladder.aggregated_asks[0]
    assert top_ask.price == 50010.0
    assert round(top_ask.total_size, 4) == 3.5  # 1.0 + 2.5
    assert top_ask.venue_sizes["BINANCE"] == 1.0
    assert top_ask.venue_sizes["COINBASE"] == 2.5
    assert top_ask.order_count == 2
    assert round(top_ask.cumulative_size, 4) == 3.5
    assert round(top_ask.cumulative_notional, 2) == 50010.0 * 3.5


def test_liquidity_depth_within_bps():
    """Verify depth_within_bps returns correct notional within basis point bands."""
    engine = ConsolidatedDepthEngine()

    # Mid price = (100.0 + 100.2) / 2 = 100.1
    # 10 bps band = 100.1 * 0.001 = 0.1001
    # Bid floor for 10 bps: 100.1 - 0.1001 = 99.9999 (only 100.0 qualifies, 99.5 does not)
    # Ask ceiling for 10 bps: 100.1 + 0.1001 = 100.2001 (only 100.2 qualifies, 100.5 does not)
    ladder = engine.observe(
        RawEvent(
            source="BINANCE",
            payload={
                "instrument": "AAPL",
                "exchange_ts": 1000.0,
                "bids": [[100.0, 10.0], [99.5, 20.0]],
                "asks": [[100.2, 5.0], [100.5, 15.0]],
            },
            receive_timestamp=1000.001,
            raw_id="r1",
        )
    )

    bid_notional, ask_notional = ladder.depth_within_bps(10.0)
    assert bid_notional == 100.0 * 10.0  # 1000.0
    assert ask_notional == 100.2 * 5.0  # 501.0

    # 100 bps band includes all levels
    bid_all, ask_all = ladder.depth_within_bps(100.0)
    assert bid_all == (100.0 * 10.0) + (99.5 * 20.0)
    assert ask_all == (100.2 * 5.0) + (100.5 * 15.0)
