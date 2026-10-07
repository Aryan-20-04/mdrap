"""Tests for SQLiteProjection: atomic rows + checkpoint commits and idempotent replay."""

import sqlite3
import pytest

from mdrap.clock import FixedClock
from mdrap.engine import Engine
from mdrap.models import RawEvent
from mdrap.projection import SQLiteProjection


def test_sqlite_projection_atomic_apply_and_checkpoint(tmp_path):
    db_path = str(tmp_path / "projection.db")
    proj = SQLiteProjection(db_path=db_path)

    assert proj.checkpoint() == -1

    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    batch = []
    for i in range(5):
        raw = RawEvent(
            source="TEST",
            payload={"instrument": "GOOGL", "price": 140.0 + i, "sequence": i + 1, "exchange_ts": 1700000000.0 + i},
            receive_timestamp=1700000000.0 + i,
        )
        state, dec = engine.step(state, raw, clock)
        batch.append(dec)

    proj.apply(batch, offset=4)
    assert proj.checkpoint() == 4
    assert proj.count_canonical() == 5
    proj.close()

    # Reopen database and verify persistent state
    proj2 = SQLiteProjection(db_path=db_path)
    assert proj2.checkpoint() == 4
    assert proj2.count_canonical() == 5
    proj2.close()


def test_sqlite_projection_idempotent_replay(tmp_path):
    db_path = str(tmp_path / "replay_projection.db")
    proj = SQLiteProjection(db_path=db_path)

    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    batch = []
    for i in range(10):
        raw = RawEvent(
            source="TEST",
            payload={"instrument": "AMZN", "price": 180.0 + i, "sequence": i + 1, "exchange_ts": 1700000000.0 + i},
            receive_timestamp=1700000000.0 + i,
        )
        state, dec = engine.step(state, raw, clock)
        batch.append(dec)

    # First apply
    proj.apply(batch, offset=9)
    assert proj.checkpoint() == 9
    assert proj.count_canonical() == 10

    # Replay same batch (simulate restart replay)
    proj.apply(batch, offset=9)
    # Count must remain 10, no duplicates inserted, no conflict errors raised
    assert proj.checkpoint() == 9
    assert proj.count_canonical() == 10
    proj.close()


def test_sqlite_projection_rollback_on_failure(tmp_path):
    db_path = str(tmp_path / "rollback.db")
    proj = SQLiteProjection(db_path=db_path)

    # Invalidate table to force an execution failure mid-transaction
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE canonical_events;")
    conn.close()

    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()
    _, dec = engine.step(state, RawEvent(source="TEST", payload={"instrument": "META", "price": 500.0}), clock)

    with pytest.raises(Exception):
        proj.apply([dec], offset=0)

    # Checkpoint must NOT advance when transaction fails!
    assert proj.checkpoint() == -1
    proj.close()
