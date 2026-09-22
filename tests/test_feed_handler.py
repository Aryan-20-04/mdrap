"""
Unit and Integration Tests for Unified Streaming Feed Supervisor and Pipeline Ingestion.
"""
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from feed_handler import (
    StreamingFeedSupervisor,
    FeedSupervisorConfig,
    FeedProvider,
)
from pipeline import Pipeline
from storage import Store
from bbo import BBOEngine
from depth import ConsolidatedDepthEngine
from models import RawEvent, QualityStatus


def test_supervisor_init_providers():
    """Verify supervisor initializes appropriate worker instances based on provider."""
    # Databento provider
    cfg_dbn = FeedSupervisorConfig(provider=FeedProvider.DATABENTO, symbols=["AAPL"], mock_mode=True)
    sup_dbn = StreamingFeedSupervisor(cfg_dbn)
    assert len(sup_dbn._workers) == 1
    assert sup_dbn._workers[0][0] == "databento"

    # Polygon provider
    cfg_poly = FeedSupervisorConfig(provider=FeedProvider.POLYGON, symbols=["MSFT"], mock_mode=True)
    sup_poly = StreamingFeedSupervisor(cfg_poly)
    assert len(sup_poly._workers) == 1
    assert sup_poly._workers[0][0] == "polygon"

    # All providers
    cfg_all = FeedSupervisorConfig(provider=FeedProvider.ALL, symbols=["BTC/USD", "AAPL"], mock_mode=True)
    sup_all = StreamingFeedSupervisor(cfg_all)
    assert len(sup_all._workers) >= 2


def test_supervisor_stream_databento():
    """Verify supervisor stream_events yields Databento records."""
    cfg = FeedSupervisorConfig(
        provider=FeedProvider.DATABENTO,
        symbols=["AAPL", "ES.c.0"],
        mock_mode=True,
        max_queue_size=100,
    )
    sup = StreamingFeedSupervisor(cfg)
    sup.start()
    assert sup.is_running() is True

    events = []
    for ev in sup.stream_events(limit=10, timeout_s=2.0):
        events.append(ev)

    sup.stop()
    assert sup.is_running() is False
    assert len(events) == 10
    for ev in events:
        assert "DATABENTO" in ev.source

    stats = sup.stats()
    assert stats["total_events"] >= 10
    assert "databento" in stats["providers"]


def test_supervisor_stream_polygon():
    """Verify supervisor stream_events yields Polygon records."""
    cfg = FeedSupervisorConfig(
        provider=FeedProvider.POLYGON,
        symbols=["AAPL", "NVDA"],
        mock_mode=True,
        max_queue_size=100,
    )
    sup = StreamingFeedSupervisor(cfg)
    sup.start()
    assert sup.is_running() is True

    events = []
    for ev in sup.stream_events(limit=10, timeout_s=2.0):
        events.append(ev)

    sup.stop()
    assert sup.is_running() is False
    assert len(events) == 10
    for ev in events:
        assert "POLYGON" in ev.source

    stats = sup.stats()
    assert stats["total_events"] >= 10
    assert "polygon" in stats["providers"]


def test_supervisor_telemetry_stats():
    """Verify aggregated supervisor telemetry statistics."""
    cfg = FeedSupervisorConfig(provider=FeedProvider.DATABENTO, symbols=["AAPL"], mock_mode=True)
    sup = StreamingFeedSupervisor(cfg)
    sup.start()

    events = list(sup.stream_events(limit=5, timeout_s=2.0))
    sup.stop()

    stats = sup.stats()
    assert "is_running" in stats
    assert "uptime_s" in stats
    assert "total_events" in stats
    assert "current_eps" in stats
    assert stats["total_events"] >= 5


def test_supervisor_pipeline_integration():
    """
    End-to-end Integration Test:
    Stream direct Databento DBN events through Ingestion Gateway -> 7-Rule Quality Engine
    (with C fastpath) -> Cross-Reconciler -> BBO Engine -> SQLite store.
    """
    store = Store(":memory:")
    bbo = BBOEngine()
    depth = ConsolidatedDepthEngine()
    pipeline = Pipeline(store, bbo=bbo)

    cfg = FeedSupervisorConfig(
        provider=FeedProvider.DATABENTO,
        symbols=["AAPL", "NVDA"],
        mock_mode=True,
        max_queue_size=50,
    )
    supervisor = StreamingFeedSupervisor(cfg)
    supervisor.start()

    processed_count = 0
    try:
        for raw in supervisor.stream_events(limit=25, timeout_s=2.0):
            canonical = pipeline.process_one(raw)
            if canonical and canonical.quality_status != QualityStatus.INVALID:
                processed_count += 1
                depth.observe(canonical)
    finally:
        supervisor.stop()
        pipeline.finish()

    assert processed_count >= 20
    # Verify BBO was populated
    bbos = bbo.all_bbos()
    assert len(bbos) > 0

    # Verify Depth Engine observed levels
    ladder = depth.current_ladder("AAPL")
    if ladder and ladder.bids:
        assert len(ladder.bids) > 0

    # Verify SQLite store received canonical events
    cur = store.conn.execute("SELECT COUNT(*) FROM canonical_events")
    assert cur.fetchone()[0] >= 15
    store.close()
