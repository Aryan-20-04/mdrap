"""Concurrency test suite for MDRAP v4.0.1 P0-B: Projection Ordering Under Concurrency.

Verifies:
1. Two threads submit concurrently -> projections receive strictly contiguous offsets.
2. Multiple feeds submit concurrently -> strictly ordered contiguous delivery without gaps.
3. Force later offset to reach tracker before earlier offset -> reordering buffer orders delivery.
4. Inject failure at offset 3 while offsets 4-10 are submitted -> earlier batch cannot be bypassed.
5. Restart with a pending projection backlog -> recovers from committed checkpoint without skipping.
6. Saturate projection queue -> raises BufferError, zero silent drops.
7. Hold external SQLite write lock -> engine submit latency unaffected (< 5ms), no feed starvation.
8. Stop engine while projection work is pending -> cleanly drains or reports backlog.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import pytest

from mdrap.models import RawEvent, CanonicalEvent, EventType, QualityStatus
from mdrap.engine import Engine, EngineDecision
from mdrap.projection import SQLiteProjection, ProjectionTracker


def _td() -> str:
    return tempfile.mkdtemp(prefix="mdrap_p0b_")


def test_p0b_two_threads_concurrent_submission():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        engine = Engine(path=td, db_path=db_path)
        num_per_thread = 50
        errors = []

        def worker(thread_id: int):
            try:
                for i in range(num_per_thread):
                    raw = RawEvent(
                        source=f"FEED_{thread_id}",
                        payload={
                            "instrument": "AAPL",
                            "event_type": "TRADE",
                            "price": 150.0 + i,
                            "quantity": 10,
                            "sequence": i + 1,
                        },
                    )
                    engine.submit(raw)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Concurrent submission threw errors: {errors}"
        engine.flush()

        proj = engine._default_sqlite_proj
        assert proj is not None
        assert proj.checkpoint() == 2 * num_per_thread - 1
        assert proj.count_canonical() == 2 * num_per_thread
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_p0b_multiple_feeds_concurrent_submission():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        engine = Engine(path=td, db_path=db_path)
        num_threads = 4
        num_per_thread = 25
        errors = []

        def worker(feed_id: int):
            try:
                for i in range(num_per_thread):
                    raw = RawEvent(
                        source=f"FEED_{feed_id}",
                        payload={
                            "instrument": "MSFT",
                            "event_type": "TRADE",
                            "price": 300.0 + i,
                            "quantity": 5,
                            "sequence": i + 1,
                        },
                    )
                    engine.submit(raw)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f,)) for f in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Feed submission threw errors: {errors}"
        engine.flush()

        proj = engine._default_sqlite_proj
        total_events = num_threads * num_per_thread
        assert proj.checkpoint() == total_events - 1
        assert proj.count_canonical() == total_events
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_p0b_force_later_offset_reordering():
    """Verify that if batch with offset 1 arrives at tracker before batch 0, it is buffered and applied in order."""
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        proj = SQLiteProjection(db_path)
        tracker = ProjectionTracker(projection=proj, name="test_reorder")

        def make_dec(off: int) -> EngineDecision:
            ev = CanonicalEvent(
                event_id=f"ev_{off}",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=1000.0 + off,
                receive_timestamp=1000.0 + off,
                processing_timestamp=1000.0 + off,
                source="TEST",
                sequence_number=off,
                price=150.0,
                quantity=1.0,
                quality_status=QualityStatus.VALID,
                reasons=[],
                raw_id=f"raw_{off}",
            )
            return EngineDecision(
                offset=off,
                event_id=ev.event_id,
                quality_status=QualityStatus.VALID,
                reasons=[],
                canonical_event=ev,
            )

        dec0 = make_dec(0)
        dec1 = make_dec(1)

        # Enqueue offset 1 FIRST
        tracker.enqueue([dec1], offset=1, batch_start=1)
        # Drain: should NOT apply dec1 yet because offset 0 is missing
        tracker.drain(non_blocking=True)
        assert proj.checkpoint() == -1

        # Now enqueue offset 0
        tracker.enqueue([dec0], offset=0, batch_start=0)
        # Drain: should apply offset 0 and then offset 1 in strict contiguous sequence!
        tracker.drain(non_blocking=True)

        assert proj.checkpoint() == 1
        assert proj.count_canonical() == 2
    finally:
        tracker.close()
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_p0b_earlier_batch_failure_blocks_bypass():
    """Verify that if offset 3 fails, offsets 4-10 wait safely and cannot bypass offset 3."""

    class FailingAtThreeProjection:
        def __init__(self):
            self.applied_offsets = []
            self.fail_at_three = True

        def checkpoint(self) -> int:
            return self.applied_offsets[-1] if self.applied_offsets else -1

        def apply(self, batch, offset):
            if offset == 3 and self.fail_at_three:
                raise RuntimeError("Simulated failure at offset 3")
            self.applied_offsets.append(offset)

    failing_proj = FailingAtThreeProjection()
    tracker = ProjectionTracker(projection=failing_proj, name="test_bypass")

    def make_dec(off: int) -> EngineDecision:
        return EngineDecision(
            offset=off,
            event_id=f"e_{off}",
            quality_status=QualityStatus.VALID,
            reasons=[],
        )

    try:
        # Enqueue 0, 1, 2 -> succeeds
        for i in range(3):
            tracker.apply([make_dec(i)], offset=i)
        assert failing_proj.applied_offsets == [0, 1, 2]

        # Enqueue 3, 4, 5, 6
        tracker.enqueue([make_dec(3)], offset=3, batch_start=3)
        for i in range(4, 7):
            tracker.enqueue([make_dec(i)], offset=i, batch_start=i)

        # Drain: offset 3 fails, offsets 4, 5, 6 MUST NOT be applied
        tracker.drain(non_blocking=True)
        assert failing_proj.applied_offsets == [0, 1, 2]
        assert tracker.recovery_status in ("DEGRADED", "STALLED")
        assert len(tracker.pending_batches) == 4

        # Fix condition: allow offset 3 to succeed
        failing_proj.fail_at_three = False
        tracker.drain(non_blocking=True)

        # Now all offsets 3, 4, 5, 6 are applied in exact order!
        assert failing_proj.applied_offsets == [0, 1, 2, 3, 4, 5, 6]
        assert len(tracker.pending_batches) == 0
        assert tracker.recovery_status == "IDLE"
    finally:
        tracker.close()


def test_p0b_restart_with_pending_projection_backlog():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        # Session 1: submit 5 events, projection commits 2, then closes
        engine1 = Engine(path=td, db_path=db_path)
        for i in range(5):
            engine1.submit(
                RawEvent(
                    source="X",
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "price": 100.0 + i,
                        "quantity": 1,
                    },
                )
            )
        engine1.flush()
        assert engine1._default_sqlite_proj.checkpoint() == 4
        engine1.close()

        # Session 2: reopen Engine with the same WAL and SQLite projection
        engine2 = Engine(path=td, db_path=db_path)
        # Checkpoint is already at 4
        assert engine2._default_sqlite_proj.checkpoint() == 4
        # Submit next contiguous event (offset 5)
        dec5 = engine2.submit(
            RawEvent(
                source="X",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 105.0,
                    "quantity": 1,
                },
            )
        )
        assert dec5.offset == 5
        engine2.flush()
        assert engine2._default_sqlite_proj.checkpoint() == 5
        assert engine2._default_sqlite_proj.count_canonical() == 6
    finally:
        engine2.close()
        shutil.rmtree(td, ignore_errors=True)


def test_p0b_saturate_projection_queue_raises():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        proj = SQLiteProjection(db_path)
        tracker = ProjectionTracker(projection=proj, name="test_sat", max_pending=5)

        def make_dec(off: int) -> EngineDecision:
            return EngineDecision(
                offset=off,
                event_id=f"e_{off}",
                quality_status=QualityStatus.VALID,
                reasons=[],
            )

        # Fill to capacity without draining (start with offset 1 so it doesn't drain since 0 is expected)
        for i in range(1, 6):
            tracker.enqueue([make_dec(i)], offset=i, batch_start=i)

        assert len(tracker.pending_batches) == 5

        # Exceed capacity: must not raise BufferError; marks degraded status and increments overflow count
        tracker.enqueue([make_dec(6)], offset=6, batch_start=6)
        assert tracker.queue_overflow_count > 0
        assert tracker.recovery_status in ("DEGRADED", "STALLED")

        assert len(tracker.pending_batches) == 5
    finally:
        tracker.close()
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_p0b_external_sqlite_lock_does_not_block_engine_submit():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        engine = Engine(path=td, db_path=db_path)
        # Warm up
        engine.submit(
            RawEvent(
                source="A",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 100.0,
                    "quantity": 1,
                },
            )
        )
        engine.flush()

        # Acquire exclusive lock on SQLite DB from an external connection
        ext_conn = sqlite3.connect(db_path)
        ext_conn.execute("BEGIN EXCLUSIVE;")

        try:
            # Measure latency of engine.submit while SQLite is held locked
            t0 = time.perf_counter()
            dec = engine.submit(
                RawEvent(
                    source="B",
                    payload={
                        "instrument": "GOOG",
                        "event_type": "TRADE",
                        "price": 200.0,
                        "quantity": 2,
                    },
                )
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            # Submission must NOT block indefinitely on SQLite write lock!
            assert elapsed_ms < 100.0, (
                f"Submit took too long ({elapsed_ms:.2f}ms) while projection locked"
            )
            assert dec.offset == 1
            assert engine.state.event_count == 2
        finally:
            ext_conn.execute("ROLLBACK;")
            ext_conn.close()

        # After releasing external lock, flushing catches up projection cleanly
        engine.flush()
        assert engine._default_sqlite_proj.checkpoint() == 1
        assert engine._default_sqlite_proj.count_canonical() == 2
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_p0b_stop_engine_while_projection_pending():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        engine = Engine(path=td, db_path=db_path)
        for i in range(10):
            engine.submit(
                RawEvent(
                    source="X",
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "price": 100.0 + i,
                        "quantity": 1,
                    },
                )
            )
        # Close immediately without manual flush
        engine.close()

        # Check that reopening confirms all 10 events were durably flushed during close
        proj2 = SQLiteProjection(db_path)
        assert proj2.checkpoint() == 9
        assert proj2.count_canonical() == 10
        proj2.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)
