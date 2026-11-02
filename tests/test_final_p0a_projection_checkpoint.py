"""Unit test suite for MDRAP v4.0.1 P0-A: Projection Checkpoint Initialization and Gap Rejection.

Verifies:
1. First batch starts at offset 0 -> succeeds and records checkpoint 0.
2. First batch starts at offset 1 when 0 is required -> raises ValueError, checkpoint stays -1.
3. First batch starts at offset 100 -> raises ValueError, checkpoint stays -1.
4. Missing first offset -> rejected, transaction rolled back.
5. Missing middle offset -> rejected, transaction rolled back, checkpoint unchanged.
6. Overlapping batches -> idempotent insert, checkpoint advances monotonically.
7. Duplicate batch delivery -> idempotent no-op, checkpoint unchanged.
8. Failed transaction followed by retry -> rolled back, retry succeeds.
9. Process restart with incomplete projection -> reloads committed checkpoint, continues contiguously.
10. Projection checkpoint corruption -> safely caught, falls back to -1 without crashing.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.engine import EngineDecision
from mdrap.projection import SQLiteProjection


def _make_temp_db() -> tuple[str, str]:
    td = tempfile.mkdtemp(prefix="mdrap_p0a_")
    db_path = os.path.join(td, "test_proj.db")
    return td, db_path


def _make_decision(
    offset: int, instrument: str = "AAPL", price: float = 150.0
) -> EngineDecision:
    ev = CanonicalEvent(
        event_id=f"ev_{offset}",
        instrument_id=instrument,
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0 + offset,
        receive_timestamp=1000.0 + offset,
        processing_timestamp=1000.0 + offset,
        source="TEST",
        sequence_number=offset,
        price=price,
        quantity=10.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
        raw_id=f"raw_{offset}",
    )
    return EngineDecision(
        offset=offset,
        event_id=ev.event_id,
        quality_status=QualityStatus.VALID,
        reasons=[],
        canonical_event=ev,
    )


def test_first_batch_starts_at_zero():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        assert proj.checkpoint() == -1

        dec0 = _make_decision(offset=0)
        proj.apply([dec0], offset=0)

        assert proj.checkpoint() == 0
        assert proj.count_canonical() == 1
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_first_batch_starts_at_one_rejected():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        assert proj.checkpoint() == -1

        dec1 = _make_decision(offset=1)
        with pytest.raises(ValueError, match="Projection gap detected"):
            proj.apply([dec1], offset=1)

        # Checkpoint MUST NOT advance!
        assert proj.checkpoint() == -1
        assert proj.count_canonical() == 0
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_first_batch_starts_at_100_rejected():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        assert proj.checkpoint() == -1

        dec100 = _make_decision(offset=100)
        with pytest.raises(ValueError, match="Projection gap detected"):
            proj.apply([dec100], offset=100)

        assert proj.checkpoint() == -1
        assert proj.count_canonical() == 0
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_missing_first_offset():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        # Attempt batch starting at offset 2 with 2 items (offsets 2 and 3)
        decs = [_make_decision(offset=2), _make_decision(offset=3)]
        with pytest.raises(ValueError, match="Projection gap detected"):
            proj.apply(decs, offset=3)

        assert proj.checkpoint() == -1
        assert proj.count_canonical() == 0
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_missing_middle_offset():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        # Apply 0, 1
        proj.apply([_make_decision(offset=0)], offset=0)
        proj.apply([_make_decision(offset=1)], offset=1)
        assert proj.checkpoint() == 1
        assert proj.count_canonical() == 2

        # Gap: jump to 3 (missing 2)
        dec3 = _make_decision(offset=3)
        with pytest.raises(ValueError, match="Projection gap detected"):
            proj.apply([dec3], offset=3)

        # Checkpoint remains at 1
        assert proj.checkpoint() == 1
        assert proj.count_canonical() == 2
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_overlapping_batches():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        # Apply [0, 1]
        proj.apply([_make_decision(0), _make_decision(1)], offset=1)
        assert proj.checkpoint() == 1
        assert proj.count_canonical() == 2

        # Overlapping batch: [1, 2, 3] (offset 1 overlaps with previously applied)
        proj.apply([_make_decision(1), _make_decision(2), _make_decision(3)], offset=3)
        assert proj.checkpoint() == 3
        # Unique canonical count must be 4: 0, 1, 2, 3
        assert proj.count_canonical() == 4
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_duplicate_batch_delivery():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        # Apply [0, 1, 2]
        batch = [_make_decision(0), _make_decision(1), _make_decision(2)]
        proj.apply(batch, offset=2)
        assert proj.checkpoint() == 2
        assert proj.count_canonical() == 3

        # Redeliver exact duplicate
        proj.apply(batch, offset=2)
        assert proj.checkpoint() == 2
        assert proj.count_canonical() == 3
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_failed_transaction_followed_by_retry():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        proj.apply([_make_decision(0)], offset=0)
        assert proj.checkpoint() == 0

        # Create a malformed object that causes an exception inside apply
        bad_dec = _make_decision(offset=1)

        # Monkey-patch to cause failure during tuple building or DB write
        class BrokenEvent:
            @property
            def event_id(self):
                raise RuntimeError("Simulated serialization/conversion failure")

        bad_dec.canonical_event = BrokenEvent()

        with pytest.raises(
            RuntimeError, match="Simulated serialization/conversion failure"
        ):
            proj.apply([bad_dec], offset=1)

        # Rolled back cleanly
        assert proj.checkpoint() == 0
        assert proj.count_canonical() == 1

        # Retry with valid decision 1
        proj.apply([_make_decision(1)], offset=1)
        assert proj.checkpoint() == 1
        assert proj.count_canonical() == 2
    finally:
        proj.close()
        shutil.rmtree(td, ignore_errors=True)


def test_process_restart_with_incomplete_projection():
    td, db_path = _make_temp_db()
    try:
        # First process session
        proj1 = SQLiteProjection(db_path)
        proj1.apply([_make_decision(0), _make_decision(1), _make_decision(2)], offset=2)
        assert proj1.checkpoint() == 2
        proj1.close()

        # Reopen in new process session
        proj2 = SQLiteProjection(db_path)
        assert proj2.checkpoint() == 2

        # Contiguous continuation succeeds
        proj2.apply([_make_decision(3)], offset=3)
        assert proj2.checkpoint() == 3
        assert proj2.count_canonical() == 4

        # Non-contiguous continuation fails
        with pytest.raises(ValueError, match="Projection gap detected"):
            proj2.apply([_make_decision(5)], offset=5)
        assert proj2.checkpoint() == 3
        assert proj2.count_canonical() == 4
    finally:
        proj2.close()
        shutil.rmtree(td, ignore_errors=True)


def test_projection_checkpoint_corruption_handled_safely():
    td, db_path = _make_temp_db()
    try:
        proj = SQLiteProjection(db_path)
        proj.apply([_make_decision(0)], offset=0)
        assert proj.checkpoint() == 0
        proj.close()

        # Corrupt the projection_checkpoints table directly
        raw_conn = sqlite3.connect(db_path)
        raw_conn.execute(
            "UPDATE projection_checkpoints SET last_offset = 'NOT_AN_INT' WHERE name = 'sqlite_canonical';"
        )
        raw_conn.commit()
        raw_conn.close()

        # Reopen: should safely handle corrupted row and fallback to -1 without crashing
        proj2 = SQLiteProjection(db_path)
        assert proj2.checkpoint() == -1
    finally:
        proj2.close()
        shutil.rmtree(td, ignore_errors=True)
