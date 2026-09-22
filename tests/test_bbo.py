import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bbo import BBOEngine, ConsolidatedBBO
from models import CanonicalEvent, EventType, QualityStatus
from storage import Store
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig


def _make_quote(
    instrument_id: str,
    source: str,
    bid_price: float,
    bid_size: float,
    ask_price: float,
    ask_size: float,
    ts: float = 1000.0,
    status: QualityStatus = QualityStatus.VALID,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"q_{source}_{ts}",
        instrument_id=instrument_id,
        event_type=EventType.QUOTE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source=source,
        sequence_number=1,
        bid_price=bid_price,
        bid_size=bid_size,
        ask_price=ask_price,
        ask_size=ask_size,
        quality_status=status,
    )


def test_bbo_synthetic_aggregation():
    """Venue A has best ask, Venue B has best bid -> synthetic tight spread."""
    engine = BBOEngine()
    
    # Venue A quotes AAPL: 150.00 / 150.50
    q1 = _make_quote("AAPL", "FEEDA", bid_price=150.00, bid_size=100.0, ask_price=150.50, ask_size=200.0, ts=1000.0)
    bbo1 = engine.observe(q1)
    assert bbo1 is not None
    assert bbo1.best_bid == 150.00
    assert bbo1.best_bid_source == "FEEDA"
    assert bbo1.best_ask == 150.50
    assert bbo1.best_ask_source == "FEEDA"
    assert round(bbo1.spread, 2) == 0.50

    # Venue B quotes AAPL: 150.20 (tighter bid!) / 150.80
    q2 = _make_quote("AAPL", "FEEDB", bid_price=150.20, bid_size=300.0, ask_price=150.80, ask_size=150.0, ts=1000.1)
    bbo2 = engine.observe(q2)
    assert bbo2 is not None
    assert bbo2.best_bid == 150.20
    assert bbo2.best_bid_source == "FEEDB"
    assert bbo2.best_ask == 150.50  # from FEEDA!
    assert bbo2.best_ask_source == "FEEDA"
    assert round(bbo2.spread, 2) == 0.30  # tighter than either venue alone
    assert bbo2.mid_price == 150.35
    assert not bbo2.is_crossed
    assert not bbo2.is_locked


def test_bbo_crossed_market_detection():
    """Cross-venue arbitrage: Venue A bid exceeds Venue B ask."""
    engine = BBOEngine()
    
    # Venue A bids aggressively: 151.00 / 152.00
    q1 = _make_quote("AAPL", "FEEDA", bid_price=151.00, bid_size=100.0, ask_price=152.00, ask_size=100.0, ts=1000.0)
    engine.observe(q1)

    # Venue B offers cheaply: 149.00 / 150.50
    q2 = _make_quote("AAPL", "FEEDB", bid_price=149.00, bid_size=100.0, ask_price=150.50, ask_size=100.0, ts=1000.1)
    bbo = engine.observe(q2)
    
    assert bbo.is_crossed
    assert not bbo.is_locked
    assert bbo.best_bid == 151.00  # from FEEDA
    assert bbo.best_ask == 150.50  # from FEEDB
    assert bbo.spread < 0  # inverted spread


def test_bbo_locked_market_detection():
    """Cross-venue locked market: Best bid equals best ask."""
    engine = BBOEngine()
    
    # Venue A bids 150.00
    q1 = _make_quote("AAPL", "FEEDA", bid_price=150.00, bid_size=100.0, ask_price=151.00, ask_size=100.0, ts=1000.0)
    engine.observe(q1)

    # Venue B asks 150.00
    q2 = _make_quote("AAPL", "FEEDB", bid_price=149.00, bid_size=100.0, ask_price=150.00, ask_size=100.0, ts=1000.1)
    bbo = engine.observe(q2)

    assert bbo.is_locked
    assert not bbo.is_crossed
    assert bbo.spread == 0.0


def test_bbo_quote_ttl_eviction():
    """Quotes older than quote_ttl_s in market time are evicted from top of book."""
    engine = BBOEngine(quote_ttl_s=2.0)
    
    # FEEDA bids 150.00 at t=1000.0
    q1 = _make_quote("AAPL", "FEEDA", bid_price=150.00, bid_size=100.0, ask_price=151.00, ask_size=100.0, ts=1000.0)
    engine.observe(q1)

    # FEEDB quotes at t=1003.0 (>2.0s later) with lower bid 148.00
    q2 = _make_quote("AAPL", "FEEDB", bid_price=148.00, bid_size=100.0, ask_price=149.00, ask_size=100.0, ts=1003.0)
    bbo = engine.observe(q2)

    # FEEDA quote should be pruned due to staleness
    assert bbo.best_bid == 148.00
    assert bbo.best_bid_source == "FEEDB"


def test_bbo_pruning_with_watchdog():
    """When a watchdog reports a source as degraded/blocked, its quotes are evicted."""
    class MockWatchdog:
        def __init__(self):
            self.active_sources = {"FEEDA", "FEEDB"}
        def is_source_active(self, source: str) -> bool:
            return source in self.active_sources

    watchdog = MockWatchdog()
    engine = BBOEngine(watchdog=watchdog)

    # Both FEEDA and FEEDB quote
    q1 = _make_quote("AAPL", "FEEDA", bid_price=150.00, bid_size=100.0, ask_price=150.50, ask_size=100.0, ts=1000.0)
    q2 = _make_quote("AAPL", "FEEDB", bid_price=150.20, bid_size=100.0, ask_price=150.80, ask_size=100.0, ts=1000.1)
    engine.observe(q1)
    bbo = engine.observe(q2)
    assert bbo.best_bid == 150.20  # from FEEDB

    # Watchdog trips: FEEDB goes silent or degraded
    watchdog.active_sources.remove("FEEDB")

    # New quote arrives from FEEDA, triggering book re-evaluation
    q3 = _make_quote("AAPL", "FEEDA", bid_price=150.05, bid_size=100.0, ask_price=150.45, ask_size=100.0, ts=1000.2)
    bbo_after = engine.observe(q3)

    # FEEDB is evicted because watchdog says it's inactive!
    assert bbo_after.best_bid == 150.05
    assert bbo_after.best_bid_source == "FEEDA"


def test_bbo_ignores_invalid_and_trades():
    """Trades and INVALID quotes never enter the order book."""
    engine = BBOEngine()

    # Trade event
    trade = CanonicalEvent(
        event_id="t1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source="FEEDA", sequence_number=1,
        price=150.0, quantity=100.0
    )
    assert engine.observe(trade) is None

    # Invalid quote (e.g. malformed or locally crossed)
    bad_quote = _make_quote("AAPL", "FEEDA", bid_price=151.0, bid_size=10.0, ask_price=149.0, ask_size=10.0, status=QualityStatus.INVALID)
    assert engine.observe(bad_quote) is None
    assert engine.current_bbo("AAPL") is None


def test_bbo_venue_attribution():
    """Tracks venue contribution to the top of book."""
    engine = BBOEngine()
    
    # FEEDA sets both bid and ask
    q1 = _make_quote("AAPL", "FEEDA", bid_price=150.0, bid_size=100.0, ask_price=151.0, ask_size=100.0, ts=1000.0)
    engine.observe(q1)

    # FEEDB improves bid only
    q2 = _make_quote("AAPL", "FEEDB", bid_price=150.5, bid_size=100.0, ask_price=151.5, ask_size=100.0, ts=1000.1)
    engine.observe(q2)

    attr = engine.venue_attribution()
    assert "FEEDA" in attr
    assert "FEEDB" in attr
    assert attr["FEEDB"]["bid_count"] == 1
    assert attr["FEEDA"]["ask_count"] == 2  # held best ask on both updates


def test_bbo_storage_persistence_and_query():
    """Verifies BBO batches write to SQLite and query back accurately."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with Store(path) as store:
            bbo = ConsolidatedBBO(
                instrument_id="AAPL", best_bid=150.25, best_bid_size=500.0, best_bid_source="FEEDX",
                best_ask=150.35, best_ask_size=600.0, best_ask_source="FEEDY",
                spread=0.10, mid_price=150.30, is_crossed=False, is_locked=False, timestamp=1000.0
            )
            store.write_bbo_batch([bbo])
            store.commit()

            rows = store.query_bbo("AAPL")
            assert len(rows) == 1
            r = rows[0]
            assert r["instrument_id"] == "AAPL"
            assert r["best_bid"] == 150.25
            assert r["best_bid_source"] == "FEEDX"
            assert r["best_ask"] == 150.35
            assert r["best_ask_source"] == "FEEDY"
            assert round(r["spread"], 2) == 0.10
            assert r["is_crossed"] == 0
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_pipeline_bbo_end_to_end():
    """Pipeline with BBOEngine persists synthetic BBOs for generated instruments."""
    store = Store(":memory:")
    bbo_engine = BBOEngine()
    pipeline = Pipeline(store, bbo=bbo_engine)

    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=2000))
    for raw, _label in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()

    bbos = store.query_bbo()
    assert len(bbos) > 0
    # Every instrument in the simulator should have an established BBO
    tickers = {r["instrument_id"] for r in bbos}
    assert "AAPL" in tickers
    store.close()


def test_bbo_serialization_with_one_sided_book():
    """Verify one-sided quotes do not produce IEEE 754 Infinity in JSON serialization."""
    import json
    engine = BBOEngine()
    # Quote with bid only, no ask price (None)
    q = CanonicalEvent(
        event_id="q_bid_only",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEED_BID_ONLY",
        sequence_number=1,
        bid_price=150.0,
        bid_size=100.0,
        ask_price=None,
        ask_size=None,
        quality_status=QualityStatus.VALID,
    )
    bbo = engine.observe(q)
    assert bbo is not None
    d = bbo.to_dict()
    assert d["best_bid"] == 150.0
    assert d["best_ask"] is None
    assert d["spread"] is None

    # Strict JSON dumps must not contain 'Infinity' or raise
    serialized = json.dumps(d)
    assert "Infinity" not in serialized
    assert "null" in serialized

    # Check cached wire buffer
    wire_bytes = engine.get_wire_bbo("AAPL")
    assert wire_bytes is not None
    assert b"Infinity" not in wire_bytes

