"""
Card #1 Verification Suite: Event ID Stability Across Restarts (S0 Remediation).

Proves that process restarts with the same DB do not cause silent data loss
via primary key collision and ON CONFLICT DO NOTHING.
"""

import os
import tempfile
import pytest

from gateway import reset_gateway_ids
from models import RawEvent
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store, StorageConflictError


def _count_canonical(store: Store) -> int:
    cur = store.conn.execute("SELECT COUNT(*) FROM canonical_events")
    return cur.fetchone()[0]


def test_t1_repro_two_runs_same_db_no_loss():
    """T1: Two sequential runs on the same DB must preserve ALL canonical events without collision."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store1 = None
    store2 = None

    try:
        store1 = Store(db_path)
        p1 = Pipeline(store=store1, flush_interval_s=0.01)
        sim1 = FeedSimulator(SimulatorConfig(seed=1, num_events=200))
        for raw, _ in sim1.generate():
            p1.process_one(raw)
        p1.finish()
        c1 = _count_canonical(store1)
        assert c1 > 0
        assert store1.conflicts == 0
        store1.close()
        store1 = None

        # Simulate process restart: fresh run_id and fresh pipeline instance
        reset_gateway_ids()
        store2 = Store(db_path)
        p2 = Pipeline(store=store2, flush_interval_s=0.01)
        sim2 = FeedSimulator(SimulatorConfig(seed=2, num_events=200))
        for raw, _ in sim2.generate():
            p2.process_one(raw)
        p2.finish()
        c2 = _count_canonical(store2)
        assert store2.conflicts == 0
        assert c2 > c1, f"Expected second run to add rows to DB, but got {c2} == {c1}"
        store2.close()
        store2 = None
    finally:
        if store1:
            try:
                store1.close()
            except Exception:
                pass
        if store2:
            try:
                store2.close()
            except Exception:
                pass
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except Exception:
                pass


def test_t2_property_k_restarts_accumulate_all_rows():
    """T2 Property: For any k restarts on the same DB, row count monotonically grows without conflicts."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = None

    try:
        total_expected = 0
        for k in range(5):
            reset_gateway_ids()
            store = Store(db_path)
            pipeline = Pipeline(store=store, flush_interval_s=0.01)
            sim = FeedSimulator(SimulatorConfig(seed=100 + k, num_events=50))
            for raw, _ in sim.generate():
                pipeline.process_one(raw)
            pipeline.finish()
            assert store.conflicts == 0
            cur_count = _count_canonical(store)
            assert cur_count > total_expected
            total_expected = cur_count
            store.close()
            store = None
    finally:
        if store:
            try:
                store.close()
            except Exception:
                pass
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except Exception:
                pass


def test_t3_collision_detection_raises_storage_conflict_error():
    """T3: When forced ID collision occurs, finish() raises StorageConflictError unless opted-out."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = None

    try:
        # Run 1: insert with fixed run ID
        reset_gateway_ids(run_id="static_run")
        store = Store(db_path)
        p1 = Pipeline(store=store, flush_interval_s=0.01)
        raw1 = RawEvent(
            source="TEST",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 10,
            },
            receive_timestamp=1000.0,
            raw_id="raw_1",
        )
        p1.process_one(raw1)
        p1.finish()
        assert store.conflicts == 0

        # Run 2: reset counter to the exact same static run ID so event_id collides
        reset_gateway_ids(run_id="static_run")
        p2 = Pipeline(store=store, flush_interval_s=0.01)
        raw2 = RawEvent(
            source="TEST",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 155.0,
                "quantity": 20,
            },
            receive_timestamp=1001.0,
            raw_id="raw_1",
        )
        p2.process_one(raw2)

        # finish() must detect the conflict and raise StorageConflictError
        with pytest.raises(StorageConflictError):
            p2.finish()

        assert store.conflicts > 0
        store.close()
        store = None
    finally:
        if store:
            try:
                store.close()
            except Exception:
                pass
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except Exception:
                pass
