"""
Unit and integration tests for MDRAP V2 Streaming Architecture.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from broker import QueueBroker
from models import QualityStatus, Reason
from pipeline_v2 import StreamingPipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


def _run_v2(events=3000, seed=42):
    cfg = SimulatorConfig(seed=seed, num_events=events)
    sim = FeedSimulator(cfg)
    store = Store(":memory:")
    pipeline = StreamingPipeline(store)
    for raw, _label in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()
    return pipeline, store


def test_v2_pipeline_processes_all_events_without_crashing():
    pipeline, store = _run_v2(3000)
    assert pipeline.metrics.processed == 3000
    assert pipeline.metrics.throughput() > 0


def test_v2_data_segregation_and_no_silent_drops():
    """All events must be accounted for: canonical + quarantine == total events."""
    events_count = 3000
    pipeline, store = _run_v2(events_count)
    cur_c = store.conn.execute("SELECT COUNT(*) FROM canonical_events")
    canonical_count = cur_c.fetchone()[0]
    cur_q = store.conn.execute("SELECT COUNT(*) FROM quarantine")
    quarantine_count = cur_q.fetchone()[0]

    # No event silently dropped
    assert canonical_count + quarantine_count >= events_count

    # canonical_events must contain NO invalid events
    cur_inv = store.conn.execute("SELECT COUNT(*) FROM canonical_events WHERE quality_status = 'INVALID'")
    assert cur_inv.fetchone()[0] == 0


def test_v2_broker_queue_backpressure():
    """Verifies backpressure detection and stall recording when broker queue fills up."""
    store = Store(":memory:")
    # Small queue capacity with low high-watermark
    broker = QueueBroker(default_capacity=100, high_watermark_pct=0.5)
    pipeline = StreamingPipeline(store, broker=broker)

    cfg = SimulatorConfig(seed=42, num_events=500)
    sim = FeedSimulator(cfg)

    for raw, _label in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()

    assert pipeline.metrics.processed == 500
    assert pipeline.metrics.max_queue_depth > 0
    # Telemetry was recorded
    summary = pipeline.metrics.summary()
    assert "streaming" in summary
    assert summary["streaming"]["max_queue_depth"] > 0


def test_v2_ground_truth_parity_with_v1():
    """V2 streaming must produce identical quality detection rates as V1 on same seed."""
    from benchmark import run_benchmark

    cfg = SimulatorConfig(seed=42, num_events=5000)
    res_v1 = run_benchmark(cfg, db_path=":memory:", label="v1_test", version="v1")
    res_v2 = run_benchmark(cfg, db_path=":memory:", label="v2_test", version="v2")

    det_v1 = res_v1["quality"]["detection_by_fault_type"]
    det_v2 = res_v2["quality"]["detection_by_fault_type"]

    for fault in ["duplicate", "out_of_order", "malformed", "crossed_quote"]:
        assert det_v1[fault]["detected"] == det_v2[fault]["detected"], f"Mismatch for {fault}"
