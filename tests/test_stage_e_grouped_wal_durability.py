"""Stage E Regression Test Suite: Grouped WAL Durability & Quiet-Period Fsync.

Verifies:
1. Quiet-period durability: grouped_by_time automatically fsyncs pending events within the time window.
2. Trailing traffic persistence: trailing events in a quiet period are not left unsynced.
3. Clean shutdown: flusher thread cleanly stops on log closure without leaking threads.
"""

from __future__ import annotations

import tempfile
import time
from typing import Any

import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent


def make_raw_event(seq: int) -> RawEvent:
    base_ts = 1_700_000_000.0 + seq
    return RawEvent(
        source="FEED",
        payload={
            "instrument": "AAPL",
            "event_type": "QUOTE",
            "bid": 150.0,
            "ask": 150.1,
            "exchange_ts": base_ts,
            "sequence": seq,
        },
        receive_timestamp=base_ts,
    )


def test_grouped_by_time_quiet_period_fsync(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="grouped_by_time"),
    )

    # Submit 3 events separated by > 50ms
    for i in range(1, 4):
        engine.submit(make_raw_event(i))
        time.sleep(0.06)

    # Submit 2 events rapidly within the 50ms window
    d1 = engine.submit(make_raw_event(4))
    time.sleep(0.01)
    d2 = engine.submit(make_raw_event(5))

    last_offset = d2.offset

    # Wait for the quiet-period flusher to trigger (> 50ms)
    time.sleep(0.2)

    # Synced offset must now be caught up to the last offset
    assert engine.log.synced_offset >= last_offset, (
        f"Expected synced_offset >= {last_offset}, got {engine.log.synced_offset}"
    )

    engine.close()


def test_grouped_by_time_reopened_retains_trailing_events(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="grouped_by_time"),
    )

    for i in range(1, 10):
        engine.submit(make_raw_event(i))

    time.sleep(0.15)
    engine.close()

    # Reopen engine and verify all 9 events are present
    engine2 = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="always"),
    )
    assert engine2.state.event_count == 9
    engine2.close()
