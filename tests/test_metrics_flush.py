"""
Tests for RunMetrics flush duration recording and stages_us attribution (Gap 3).
"""

from metrics import RunMetrics
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


def test_run_metrics_record_flush():
    metrics = RunMetrics()
    metrics.record_flush(0.0025)  # 2.5 ms
    metrics.record_flush(0.0015)  # 1.5 ms

    summary = metrics.summary()
    assert "stages_us" in summary
    assert "wal_storage_flush" in summary["stages_us"]
    flush_stage = summary["stages_us"]["wal_storage_flush"]
    assert flush_stage["p50"] > 0.0
    assert 1400.0 <= flush_stage["p50"] <= 2600.0


def test_pipeline_live_flush_attribution():
    store = Store(":memory:")
    pipeline = Pipeline(store=store, flush_interval_s=0.0001)
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=20))

    for raw, _label in sim.generate():
        pipeline.process_one(raw)

    pipeline.finish()
    summary = pipeline.metrics.summary()
    assert "stages_us" in summary
    assert "wal_storage_flush" in summary["stages_us"]
    assert summary["stages_us"]["wal_storage_flush"]["p50"] > 0.0
    store.close()
