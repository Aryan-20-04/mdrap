"""Unit test suite for Phase 2: Commit & Projection Semantics (E15).

Verifies:
- Authoritative state and WAL commit independently of downstream projections.
- Flaky/failing projection does not roll back EngineState or corrupt WAL offsets.
- Multi-projection isolation: Healthy projections commit successfully even if a peer fails.
- Idempotent replay and reconstruction of projections via rebuild_projection().
"""

import os
import shutil
import tempfile
import pytest

from mdrap.engine import Engine
from mdrap.models import RawEvent
from mdrap.projection import SQLiteProjection


def _td():
    return tempfile.mkdtemp(prefix="phase2_proj_")


class FlakyProjection:
    def __init__(self, should_fail: bool):
        self.applied = []
        self.should_fail = should_fail
        self._last_offset = -1

    def apply(self, decisions, offset):
        if self.should_fail:
            raise IOError("External projection backend unavailable")
        self.applied.extend(decisions)
        self._last_offset = offset

    def checkpoint(self):
        return self._last_offset


def test_e15_fixed_projection_isolation_and_authoritative_commit():
    td = _td()
    try:
        engine = Engine(path=td)
        good_proj = FlakyProjection(should_fail=False)
        bad_proj = FlakyProjection(should_fail=True)
        engine.subscribe(good_proj)
        engine.subscribe(bad_proj)

        raw = RawEvent(
            source="X",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 10,
            },
        )
        dec = engine.submit(raw)
        assert dec is not None

        # Authoritative commit succeeded
        assert engine.state.event_count == 1
        assert engine.log.next_offset == 1

        # Good projection received the record
        assert len(good_proj.applied) == 1
        assert good_proj.checkpoint() == 0

        # Submitting second event also succeeds authoritatively
        raw2 = RawEvent(
            source="X",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 151.0,
                "quantity": 5,
            },
        )
        dec2 = engine.submit(raw2)
        assert dec2 is not None
        assert engine.state.event_count == 2
        assert engine.log.next_offset == 2
        assert len(good_proj.applied) == 2
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_sqlite_projection_idempotency_on_rebuild():
    td = _td()
    db_path = os.path.join(td, "proj.db")
    try:
        engine = Engine(path=td, db_path=db_path)
        for i in range(5):
            engine.submit(
                RawEvent(
                    source="NASDAQ",
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "price": 150.0 + i,
                        "quantity": 10,
                    },
                )
            )
        assert engine.state.event_count == 5

        # Query projection row count
        conn = engine._default_sqlite_proj._conn
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM canonical_events;")
        count = cur.fetchone()[0]
        assert count == 5

        # Rebuilding projection must be idempotent (zero duplicates or constraint violations)
        engine.rebuild_projection(engine._default_sqlite_proj)
        cur.execute("SELECT COUNT(*) FROM canonical_events;")
        assert cur.fetchone()[0] == 5
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)
