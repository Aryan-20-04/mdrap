"""
Tests for Advanced Multi-Fault Chaos Drills (v2.6 Reliability Milestone).

Verifies:
1. Burst packet loss drill with FeedRecoveryStateMachine gap stitching.
2. Sequence reversal and inverted arrival window buffering.
3. Concurrent SQLite busy lock exponential backoff and retry durability.
4. Complete network partition isolation and BBO multi-venue failover.
5. All 8 drills passing together cleanly with zero data loss.
"""

from __future__ import annotations

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.chaos import ChaosEngine


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass


def test_burst_packet_loss_drill(temp_db):
    """Verify burst packet loss detection, state machine replay request, and gap stitching."""
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_burst_packet_loss_drill(
        target_source="FEEDX", total_events=1000, loss_size=50
    )
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "stitched all 50 missing packets" in res.details


def test_sequence_reversal_drill(temp_db):
    """Verify scrambled/reversed arrival windows are ordered without drops."""
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_sequence_reversal_drill(
        target_source="FEEDY", total_events=400, window_size=10
    )
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "zero drops" in res.details


def test_sqlite_locked_backoff_drill(temp_db):
    """Verify concurrent SQLite lock exponential backoff retry survives without data loss."""
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_sqlite_locked_backoff_drill(total_events=150)
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "zero loss" in res.details


def test_network_partition_drill(temp_db):
    """Verify total venue network partition trips watchdog silence and routes secondary."""
    engine = ChaosEngine(db_path=temp_db)
    res = engine.run_network_partition_drill(
        primary_source="FEEDX", secondary_source="FEEDY", total_events=1200
    )
    assert res.passed is True
    assert res.data_loss_count == 0
    assert "Zero dropped events: 0" in res.details


def test_complete_chaos_suite_v26(temp_db):
    """Run full 8-drill chaos certification suite."""
    engine = ChaosEngine(db_path=temp_db)
    results = engine.run_all_drills()
    assert len(results) == 8
    for r in results:
        assert r.passed is True, f"Drill {r.drill_name} failed: {r.details}"
        assert r.data_loss_count == 0, (
            f"Drill {r.drill_name} had data loss: {r.data_loss_count}"
        )
