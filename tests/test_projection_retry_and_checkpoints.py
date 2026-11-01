"""Tests for reliable projection retry, checkpointing, and async worker resilience."""

import sqlite3
from unittest.mock import MagicMock
import pytest

from mdrap.clock import FixedClock
from mdrap.engine import Engine
from mdrap.models import RawEvent
from mdrap.projection import AsyncProjectionWorker, SQLiteProjection


def test_sqlite_projection_retry_on_locked_database(tmp_path):
    """Verify SQLiteProjection retries with exponential backoff on database is locked."""
    db_path = str(tmp_path / "locked_test.db")
    proj = SQLiteProjection(db_path=db_path)

    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    raw = RawEvent(
        source="TEST",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 10.0,
            "exchange_ts": 1700000000.0,
        },
        receive_timestamp=1700000000.0,
    )
    state, dec = engine.step(state, raw, clock)

    # Wrap connection to simulate 2 locked errors before success on BEGIN IMMEDIATE
    real_conn = proj._conn
    attempts = [0]

    class FlakyConnProxy:
        def __init__(self, target):
            self._target = target

        def cursor(self):
            cur = self._target.cursor()

            class FlakyCursorProxy:
                def execute(self, sql, *args, **kwargs):
                    if isinstance(sql, str) and sql.strip().startswith(
                        "BEGIN IMMEDIATE"
                    ):
                        attempts[0] += 1
                        if attempts[0] <= 2:
                            raise sqlite3.OperationalError(
                                "database is locked (simulated contention)"
                            )
                    return cur.execute(sql, *args, **kwargs)

                def executemany(self, *args, **kwargs):
                    return cur.executemany(*args, **kwargs)

                def fetchone(self):
                    return cur.fetchone()

                def fetchall(self):
                    return cur.fetchall()

                def __getattr__(self, name):
                    return getattr(cur, name)

            return FlakyCursorProxy()

        def __getattr__(self, name):
            return getattr(self._target, name)

    proj._conn = FlakyConnProxy(real_conn)

    proj.apply([dec], offset=0, max_retries=5, retry_delay=0.01)

    assert attempts[0] == 3  # Failed 2 times, succeeded on 3rd attempt
    assert proj.checkpoint() == 0
    assert proj.count_canonical() == 1
    proj._conn = real_conn
    proj.close()


def test_async_projection_worker_retry_transient_error(tmp_path):
    """Verify AsyncProjectionWorker retries transient batch failures before giving up."""

    class FlakyMockProjection:
        def __init__(self):
            self.fail_count = 2  # Fail first 2 attempts, succeed on 3rd
            self.applied_batches = []
            self._checkpoint = -1

        def apply(self, batch, offset):
            if self.fail_count > 0:
                self.fail_count -= 1
                raise sqlite3.OperationalError(
                    "database is locked (transient simulated failure)"
                )
            self.applied_batches.extend(batch)
            self._checkpoint = offset

        def checkpoint(self):
            return self._checkpoint

    mock_proj = FlakyMockProjection()
    worker = AsyncProjectionWorker(
        projection=mock_proj,
        batch_size=5,
        flush_interval_sec=0.02,
        max_retries=4,
        retry_backoff_sec=0.01,
    )

    for i in range(3):
        worker.enqueue({"event_id": f"e_{i}"}, offset=i)

    worker.flush(timeout=2.0)
    worker.close(timeout=2.0)

    stats = worker.stats()
    assert stats["errors_total"] == 0  # 0 unrecoverable errors
    assert len(mock_proj.applied_batches) == 3
    assert worker.checkpoint() == 2
    assert mock_proj.checkpoint() == 2


def test_projection_checkpoint_monotonicity_negative_offset(tmp_path):
    """Verify projection checkpoint is not degraded by offset < 0."""
    db_path = str(tmp_path / "mono_check.db")
    proj = SQLiteProjection(db_path=db_path)

    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    raw1 = RawEvent(
        source="TEST",
        payload={
            "instrument": "MSFT",
            "event_type": "TRADE",
            "price": 300.0,
            "quantity": 1.0,
            "exchange_ts": 1700000000.0,
        },
        receive_timestamp=1700000000.0,
    )
    state, dec1 = engine.step(state, raw1, clock)

    proj.apply([dec1], offset=42)
    assert proj.checkpoint() == 42
    assert proj.count_canonical() == 1

    # Applying without offset (offset=-1) does NOT wipe out or reset checkpoint
    raw2 = RawEvent(
        source="TEST",
        payload={
            "instrument": "MSFT",
            "event_type": "TRADE",
            "price": 301.0,
            "quantity": 1.0,
            "exchange_ts": 1700000001.0,
        },
        receive_timestamp=1700000001.0,
    )
    state, dec2 = engine.step(state, raw2, clock)
    proj.apply([dec2], offset=-1)

    assert proj.checkpoint() == 42
    assert proj.count_canonical() == 2
    proj.close()
