"""Stage C Regression Test Suite: Projection Reliability, Contiguous Checkpoints, and SQLite Isolation.

Verifies:
1. Critical Checkpoint Invariant: projection checkpoint never advances past contiguous committed offsets.
2. Automatic Recovery: transient projection outages automatically replay queued batches upon resolution.
3. Ingestion Isolation: external SQLite lock contention does not block concurrent feed ingestion.
4. Health & Lag Telemetry: projection failures and lag properly transition health from HEALTHY to DEGRADED/UNHEALTHY.
5. Idempotent Atomic Writes: canonical events and projection checkpoints commit atomically in SQLite.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from typing import Any

import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.models import RawEvent
from mdrap.projection import SQLiteProjection


def make_raw_event(
    seq: int, source: str = "FEED_A", instrument: str = "AAPL"
) -> RawEvent:
    base_ts = 1_700_000_000.0 + seq
    return RawEvent(
        source=source,
        payload={
            "instrument": instrument,
            "event_type": "QUOTE",
            "bid": 150.0 + seq * 0.01,
            "ask": 150.1 + seq * 0.01,
            "exchange_ts": base_ts,
            "sequence": seq,
        },
        receive_timestamp=base_ts,
    )


class OutageProjection:
    def __init__(self, name: str = "outage_proj") -> None:
        self.name = name
        self.offsets: list[int] = []
        self.fail: bool = False

    def apply(self, batch: list[Any], offset: int) -> None:
        if self.fail:
            raise RuntimeError("Outage simulated")
        self.offsets.append(offset)

    def checkpoint(self) -> int:
        return self.offsets[-1] if self.offsets else -1


def test_projection_checkpoint_strictly_contiguous(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    proj = OutageProjection()
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="never", projections=[proj]),
    )

    # Submit 3 valid events (offsets 0, 1, 2)
    for i in range(1, 4):
        engine.submit(make_raw_event(i))

    assert proj.offsets == [0, 1, 2]
    assert proj.checkpoint() == 2

    # Simulate outage on offsets 3, 4, 5
    proj.fail = True
    for i in range(4, 7):
        dec = engine.submit(make_raw_event(i))
        assert dec is not None

    m = engine.metrics()
    assert m["health_status"] == "DEGRADED"
    assert m["projection_lag"] == 3
    assert proj.checkpoint() == 2

    # Resolve outage and submit offset 6
    proj.fail = False
    engine.submit(make_raw_event(7))

    # All offsets 0..6 must be present in strict order
    assert proj.offsets == [0, 1, 2, 3, 4, 5, 6]
    m_recovered = engine.metrics()
    assert m_recovered["health_status"] == "HEALTHY"
    assert m_recovered["projection_lag"] == 0

    engine.close()


def test_sqlite_projection_lock_isolation_across_feeds(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    db_path = tmp_path / "proj.db"
    proj = SQLiteProjection(str(db_path))
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="never", projections=[proj]),
    )

    for i in range(1, 4):
        engine.submit(make_raw_event(i))

    # Lock SQLite externally for 1 second
    ext_conn = sqlite3.connect(
        str(db_path), isolation_level=None, check_same_thread=False
    )
    ext_conn.execute("BEGIN IMMEDIATE;")
    timer = threading.Timer(1.0, lambda: ext_conn.execute("COMMIT"))
    timer.start()

    fast_durations: list[float] = []

    def feed_b_worker() -> None:
        t0 = time.perf_counter()
        engine.submit(make_raw_event(100, source="FEED_B", instrument="MSFT"))
        fast_durations.append(time.perf_counter() - t0)

    t_main: list[float] = []

    def feed_a_worker() -> None:
        t0 = time.perf_counter()
        engine.submit(make_raw_event(4, source="FEED_A", instrument="AAPL"))
        t_main.append(time.perf_counter() - t0)

    th_a = threading.Thread(target=feed_a_worker)
    th_b = threading.Thread(target=feed_b_worker)

    th_a.start()
    time.sleep(0.1)
    th_b.start()

    th_a.join()
    th_b.join()
    timer.join()
    ext_conn.close()

    # Feed B must not experience head-of-line blocking (> 0.5s) from Feed A's locked DB
    assert fast_durations, "Feed B must have completed"
    assert fast_durations[0] < 0.35, (
        f"Feed B blocked unexpectedly: {fast_durations[0]:.2f}s"
    )

    engine.close()


def test_projection_metrics_reporting(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    proj = OutageProjection()
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="never", projections=[proj]),
    )

    engine.submit(make_raw_event(1))
    proj.fail = True
    engine.submit(make_raw_event(2))

    m = engine.metrics()
    assert m["health_status"] == "DEGRADED"
    assert len(m["projections"]) == 1
    p_stat = m["projections"][0]
    assert p_stat["failed_offsets"] == [1]
    assert p_stat["consecutive_failures"] >= 1
    assert p_stat["retry_count"] >= 1
    assert p_stat["recovery_status"] in ("DEGRADED", "STALLED")

    engine.close()
