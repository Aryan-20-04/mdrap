"""
Unit and integration tests for MDRAP System Limitations, Stress Boundaries, and Microstructure Capabilities.

Evaluates:
1. Instrument Universe Scaling & C Fastpath Boundary (Capacity & Graceful Fallback)
2. Order Book Depth Exhaustion & TCA Slippage Boundary (Severe Liquidity Shortfall)
3. Multi-Venue Crossed Quotes & Arbitrage Stress (Crossed Market Detection & Quarantine)
4. High-Throughput Burst Saturation & Tail Latency Profile (p50, p95, p99, p99.9)
5. Live Feed Failure & Watchdog Silence Boundary
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analytics import MarketAnalytics
from bbo import BBOEngine
from depth import ConsolidatedDepthEngine
from fastpath import FastQualityEngine, _INSTRUMENTS
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from pipeline import Pipeline
from quality import QualityEngine
from reconciliation import ReliabilityTracker
from simulator import FeedSimulator, SimulatorConfig
from storage import Store
from watchdog import SourceState, SourceWatchdog


def test_instrument_universe_capacity_and_fallback_limit():
    """
    Test the C fastpath static boundary vs dynamic Python fallback.
    The C engine fastpath.c has #define MAX_INSTRUMENTS 64.
    Verify that instruments within the mapped set run fast, and
    an expanded universe (e.g. 100 instruments) runs safely without memory corruption.
    """
    engine = FastQualityEngine()

    # 1. Test standard mapped instruments (e.g. AAPL, MSFT, NVDA)
    event_mapped = CanonicalEvent(
        event_id="c_mapped_1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDX",
        sequence_number=1,
        price=150.0,
        quantity=10.0,
    )
    res_mapped = engine.evaluate(event_mapped)
    assert res_mapped.quality_status == QualityStatus.VALID
    assert len(res_mapped.reasons) == 0

    # 2. Test S&P 500 universe scaling: 500 distinct instruments through C Fastpath
    for i in range(500):
        sym = f"SP500_{i:03d}"
        ev = CanonicalEvent(
            event_id=f"c_sp_{i}",
            instrument_id=sym,
            event_type=EventType.QUOTE,
            exchange_timestamp=1000.0 + i,
            receive_timestamp=1000.001 + i,
            processing_timestamp=1000.002 + i,
            source="FEEDX",
            sequence_number=i + 1,
            bid_price=100.0 + (i * 0.1),
            ask_price=100.5 + (i * 0.1),
            bid_size=50.0,
            ask_size=50.0,
        )
        res = engine.evaluate(ev)
        assert res.quality_status == QualityStatus.VALID

    # Verify that all 500+ instruments were handled by C hot-path without invoking fallback
    assert len(engine._inst_map) >= 500
    assert engine._fallback_engine is None  # Pure C execution!

    # 3. Test boundary behavior when instrument index reaches 8,192
    # Force engine to assign ID 8192 to test graceful fallback mechanism
    engine._inst_map["OVERFLOW_TICKER"] = 8192
    ev_overflow = CanonicalEvent(
        event_id="c_overflow_1",
        instrument_id="OVERFLOW_TICKER",
        event_type=EventType.QUOTE,
        exchange_timestamp=2000.0,
        receive_timestamp=2000.001,
        processing_timestamp=2000.002,
        source="FEEDX",
        sequence_number=1,
        bid_price=50.0,
        ask_price=50.2,
        bid_size=10.0,
        ask_size=10.0,
    )
    res_overflow = engine.evaluate(ev_overflow)
    assert res_overflow.quality_status == QualityStatus.VALID
    assert engine._fallback_engine is not None  # Graceful fallback triggered safely!


def test_order_book_depth_exhaustion_and_tca_slippage_boundary():
    """
    Test institutional order book walking when requested size exceeds ALL available depth.
    Tests capability: accurate slippage, VWAP, venue attribution, and shortfall detection.
    Tests limitation: book exhaustion boundary (is_fully_filled == False).
    """
    engine = ConsolidatedDepthEngine()
    symbol = "BTC/USD"

    # Seed 3 venues with thin depth (Total available ask depth = 1.0 + 2.0 + 3.0 = 6.0 units)
    engine.observe(RawEvent(
        source="BINANCE",
        payload={
            "instrument": symbol,
            "exchange_ts": 1000.0,
            "bids": [[65000.0, 2.0]],
            "asks": [[65010.0, 1.0]],
        },
        receive_timestamp=1000.001,
        raw_id="b1",
    ))
    engine.observe(RawEvent(
        source="COINBASE",
        payload={
            "instrument": symbol,
            "exchange_ts": 1000.01,
            "bids": [[64995.0, 5.0]],
            "asks": [[65015.0, 2.0]],
        },
        receive_timestamp=1000.011,
        raw_id="c1",
    ))
    engine.observe(RawEvent(
        source="KRAKEN",
        payload={
            "instrument": symbol,
            "exchange_ts": 1000.02,
            "bids": [[64990.0, 4.0]],
            "asks": [[65020.0, 3.0]],
        },
        receive_timestamp=1000.021,
        raw_id="k1",
    ))

    ladder = engine.current_ladder(symbol)
    assert ladder is not None

    # Case A: Normal order within depth (Target: 2.5 units)
    slice_normal = ladder.compute_vwap(side="BUY", target_size=2.5)
    assert slice_normal.is_fully_filled is True
    assert slice_normal.filled_size == 2.5
    # 1.0 @ 65010 (Binance) + 1.5 @ 65015 (Coinbase) = (65010 + 97522.5) / 2.5 = 65013.0
    assert abs(slice_normal.vwap_price - 65013.0) < 0.01
    assert slice_normal.venue_breakdown["BINANCE"] == 1.0
    assert slice_normal.venue_breakdown["COINBASE"] == 1.5

    # Case B: Liquidity Exhaustion / Shortfall (Target: 20.0 units, but only 6.0 available)
    slice_exhausted = ladder.compute_vwap(side="BUY", target_size=20.0)
    assert slice_exhausted.is_fully_filled is False
    assert slice_exhausted.filled_size == 6.0  # Consumed 100% of book
    assert slice_exhausted.target_size == 20.0
    # VWAP of all 6 units: (1*65010 + 2*65015 + 3*65020) / 6 = (65010 + 130030 + 195060) / 6 = 390100 / 6 = 65016.67
    assert abs(slice_exhausted.vwap_price - 65016.67) < 0.05
    assert slice_exhausted.venue_breakdown["BINANCE"] == 1.0
    assert slice_exhausted.venue_breakdown["COINBASE"] == 2.0
    assert slice_exhausted.venue_breakdown["KRAKEN"] == 3.0
    assert slice_exhausted.slippage_bps > 0.0


def test_multi_venue_crossed_market_and_arbitrage_detection():
    """
    Test capability to detect multi-venue arbitrage & crossed market condition (Bid > Ask).
    Venue A: Bid $100.50 (Coinbase)
    Venue B: Ask $100.20 (Binance)
    Consolidated spread is negative (-$0.30). The engine must flag crossed book.
    """
    engine = ConsolidatedDepthEngine()
    symbol = "ETH/USD"

    engine.observe(RawEvent(
        source="COINBASE",
        payload={
            "instrument": symbol,
            "exchange_ts": 1000.0,
            "bids": [[100.50, 10.0]],
            "asks": [[100.70, 5.0]],
        },
        receive_timestamp=1000.001,
        raw_id="c_arb",
    ))
    engine.observe(RawEvent(
        source="BINANCE",
        payload={
            "instrument": symbol,
            "exchange_ts": 1000.05,
            "bids": [[100.00, 10.0]],
            "asks": [[100.20, 5.0]],
        },
        receive_timestamp=1000.051,
        raw_id="b_arb",
    ))

    ladder = engine.current_ladder(symbol)
    assert ladder is not None
    assert ladder.is_crossed is True
    assert round(ladder.asks[0].price - ladder.bids[0].price, 2) == -0.30
    assert ladder.bids[0].price == 100.50
    assert ladder.asks[0].price == 100.20
    assert len(ladder.crossed_opportunities) > 0
    assert ladder.crossed_opportunities[0]["bid_venue"] == "COINBASE"
    assert ladder.crossed_opportunities[0]["ask_venue"] == "BINANCE"

    # Also verify BBOEngine flags crossed quote
    bbo_engine = BBOEngine()
    bbo = bbo_engine.observe(CanonicalEvent(
        event_id="arb_1",
        instrument_id="ETH/USD",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="COINBASE",
        sequence_number=1,
        bid_price=100.50,
        bid_size=10.0,
        ask_price=100.60,
        ask_size=10.0,
    ))
    bbo2 = bbo_engine.observe(CanonicalEvent(
        event_id="arb_2",
        instrument_id="ETH/USD",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.1,
        receive_timestamp=1000.101,
        processing_timestamp=1000.102,
        source="BINANCE",
        sequence_number=2,
        bid_price=100.10,
        bid_size=10.0,
        ask_price=100.20,
        ask_size=5.0,
    ))
    assert bbo2 is not None
    assert bbo2.is_crossed is True


def test_watchdog_silence_failover_boundary():
    """
    Test watchdog reaction when an exchange feed suddenly goes silent (disconnects).
    Validates silence threshold (2.0s) and hysteresis recovery.
    """
    reliability = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability, silence_threshold_s=2.0)

    ev1 = CanonicalEvent(
        event_id="w1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source="BINANCE", sequence_number=1,
        price=150.0, quantity=10.0
    )
    ev2 = CanonicalEvent(
        event_id="w2", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source="COINBASE", sequence_number=1,
        price=150.0, quantity=10.0
    )
    watchdog.observe(ev1)
    watchdog.observe(ev2)
    assert watchdog.source_states()["BINANCE"] == SourceState.HEALTHY.value
    assert watchdog.source_states()["COINBASE"] == SourceState.HEALTHY.value

    # Binance keeps streaming at t=1003.0 (>2s gap for Coinbase)
    ev3 = CanonicalEvent(
        event_id="w3", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1003.0, receive_timestamp=1003.001,
        processing_timestamp=1003.002, source="BINANCE", sequence_number=2,
        price=150.2, quantity=5.0
    )
    alerts = watchdog.observe(ev3)
    assert watchdog.source_states()["COINBASE"] == SourceState.SILENT.value
    assert any(a.source == "COINBASE" and a.alert_type == "SILENCE" for a in alerts)
    assert watchdog.active_sources() == ["BINANCE"]

    # Coinbase reconnects at t=1004.0
    ev4 = CanonicalEvent(
        event_id="w4", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1004.0, receive_timestamp=1004.001,
        processing_timestamp=1004.002, source="COINBASE", sequence_number=2,
        price=150.1, quantity=15.0
    )
    rec_alerts = watchdog.observe(ev4)
    assert watchdog.source_states()["COINBASE"] == SourceState.HEALTHY.value
    assert any(a.source == "COINBASE" and a.alert_type == "RECOVERY" for a in rec_alerts)


def test_burst_throughput_saturation_and_latency_profile():
    """
    Stress-test the end-to-end pipeline under a 3,000-event burst.
    Measures throughput (eps) and tail latency percentiles (p50, p95, p99).
    Verifies zero silent drops and zero data loss.
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        pipeline = Pipeline(store=store)
        sim_cfg = SimulatorConfig(seed=123, num_events=3000)
        sim = FeedSimulator(sim_cfg)

        t0 = time.perf_counter()
        for raw, truth in sim.generate():
            pipeline.process_one(raw)
        pipeline.finish()
        total_time = time.perf_counter() - t0

        summary = pipeline.metrics.summary()
        proc_lat = summary.get("processing_latency_us", {})
        throughput = 3000 / total_time
        assert throughput > 500.0
        assert pipeline.metrics.processed == 3000
        assert pipeline.metrics.dropped == 0
        assert "p50" in proc_lat
        assert "p95" in proc_lat
        assert "p99" in proc_lat
        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
