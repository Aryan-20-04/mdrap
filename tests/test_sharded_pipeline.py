"""
Tests for MDRAP Multi-Process Sharded Pipeline Engine (§25 V4).
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from models import RawEvent
from sharded_pipeline import ShardedPipeline, run_sharded_benchmark


def test_sharded_pipeline_basic_lifecycle():
    pipeline = ShardedPipeline(num_workers=2)
    pipeline.start()

    # Send 100 events across 2 workers
    for i in range(100):
        raw = RawEvent(
            source="FEED_TEST",
            payload={
                "instrument": "AAPL" if i % 2 == 0 else "MSFT",
                "event_type": "TRADE",
                "price": 150.0 + i,
                "quantity": 100.0,
                "exchange_ts": 1000.0 + i,
                "sequence": i + 1,
            },
            receive_timestamp=1000.0 + i + 0.001,
            raw_id=f"t-{i}",
        )
        pipeline.dispatch(raw)

    stats = pipeline.stop()
    assert len(stats) == 2
    total_processed = sum(w["processed"] for w in stats)
    assert total_processed == 100


def test_sharded_pipeline_benchmark():
    res = run_sharded_benchmark(num_workers=2, total_events=2000)
    assert res["total_processed"] == 2000
    assert res["num_workers"] == 2
    assert res["aggregate_eps"] > 1000.0
    assert len(res["worker_stats"]) == 2
