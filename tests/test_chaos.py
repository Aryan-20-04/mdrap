import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chaos import ChaosEngine, drop_source_window
from simulator import FeedSimulator, SimulatorConfig


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_legacy_drop_source_window():
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=50))
    gen = drop_source_window(sim.generate(), source="FEEDX", start_count=5, duration_count=5)
    dropped = 0
    total = 0
    for raw, label, was_dropped in gen:
        total += 1
        if was_dropped:
            dropped += 1
    assert dropped == 5
    assert total == 50


def test_feed_kill_drill(temp_db):
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_feed_kill_drill(target_source="FEEDX", total_events=2000)
    assert res.passed is True
    assert res.data_loss_count == 0
    assert res.target_source == "FEEDX"


def test_network_jitter_drill(temp_db):
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_network_jitter_drill(target_source="FEEDY", total_events=2000)
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "Caught" in res.details


def test_burst_drill(temp_db):
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_burst_drill(target_source="FEEDZ", total_events=1500)
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "Filtered" in res.details


def test_storage_outage_drill(temp_db):
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_storage_outage_drill(total_events=1500)
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "Buffers survived" in res.details


def test_run_all_drills(temp_db):
    engine = ChaosEngine(db_path=temp_db)
    results = engine.run_all_drills()
    assert len(results) == 4
    for r in results:
        assert r.passed is True
        assert r.data_loss_count == 0
