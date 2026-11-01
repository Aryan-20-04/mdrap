"""Unit test suite for Phase 1 Audit Remediation (WAL, Recovery, Durability).

Verifies fixes for:
- E1: Oversize payload accounting and offset alignment
- E6: Pure deterministic replay without external on_gap callbacks
- E7: Preservation of session_id in WAL serialization
- E8/E12: Proper non-forced fsync in append_batch for grouped policies
- E11: Snapshot offset tied to actual last_applied_offset
"""

import json
import os
import shutil
import tempfile
import pytest

from mdrap.engine import Engine
from mdrap.ingestlog import IngestLog
from mdrap.models import QualityStatus, RawEvent, Reason


def _td():
    return tempfile.mkdtemp(prefix="phase1_fix_")


def test_e1_fixed_oversize_payload_accounting():
    td = _td()
    try:
        engine = Engine(path=td, max_payload_bytes=80)
        raw_valid1 = RawEvent(source="feed1", payload={"instrument": "A", "event_type": "TRADE", "price": 10.0, "quantity": 1})
        raw_over = RawEvent(source="feed1", payload={"msg": "x" * 150})
        raw_valid2 = RawEvent(source="feed1", payload={"instrument": "A", "event_type": "TRADE", "price": 10.5, "quantity": 1})

        # Submit mixed batch
        decs = engine.submit([raw_valid1, raw_over, raw_valid2])
        assert len(decs) == 3

        # First valid event got offset 0
        assert decs[0].offset == 0
        assert decs[0].quality_status == QualityStatus.VALID

        # Oversize event got offset -1 and quarantine row
        assert decs[1].offset == -1
        assert decs[1].quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in decs[1].reasons
        assert decs[1].quarantine_row is not None

        # Second valid event got offset 1 (NO DUPLICATE OFFSET!)
        assert decs[2].offset == 1
        assert decs[2].quality_status == QualityStatus.VALID

        # State counters aligned
        assert engine.state.event_count == 2
        assert engine.state.observed_count == 3
        assert engine.log.next_offset == 2
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e6_fixed_replay_purity_no_gap_side_effects():
    td = _td()
    calls = []
    def on_gap_hook(*args):
        calls.append(args)

    try:
        engine = Engine(path=td, on_gap=on_gap_hook)
        engine.submit(RawEvent(source="S", payload={"instrument": "A", "event_type": "TRADE", "sequence": 1, "price": 10.0, "quantity": 1}))
        engine.submit(RawEvent(source="S", payload={"instrument": "A", "event_type": "TRADE", "sequence": 5, "price": 10.0, "quantity": 1}))
        assert len(calls) == 1
        engine.close()

        # Restart engine and replay historical log
        calls.clear()
        recov_engine = Engine(path=td, on_gap=on_gap_hook)
        recov_engine.replay(from_offset=0)
        # Replay must NOT trigger on_gap side effects
        assert len(calls) == 0
        recov_engine.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e7_fixed_session_id_persisted_in_wal():
    td = _td()
    try:
        wal = IngestLog(td)
        raw = RawEvent(source="SRC", payload={"val": 123}, session_id="SESS_ALPHA")
        off = wal.append(raw)
        wal.close()

        wal2 = IngestLog(td)
        res = wal2.seek_record(off)
        wal2.close()
        assert res is not None
        _, loaded_raw = res
        assert loaded_raw.session_id == "SESS_ALPHA"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e8_e12_fixed_append_batch_grouped_durability():
    td = _td()
    try:
        wal = IngestLog(td, fsync_policy="grouped_by_time")
        raws = [RawEvent(source="S", payload={"n": i}) for i in range(5)]
        offsets = wal.append_batch(raws)
        assert len(offsets) == 5

        # Under grouped_by_time with < 50ms, append_batch buffers without blocking on fsync
        # and flush() guarantees durability
        wal.flush()
        assert wal._synced_offset == offsets[-1]
        wal.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e11_fixed_snapshot_offset_tied_to_applied_offset():
    td = _td()
    snap_path = os.path.join(td, "snap.json")
    try:
        engine = Engine(path=td, max_payload_bytes=50)
        engine.submit(RawEvent(source="S", payload={"msg": "ok"}))  # offset 0
        engine.submit(RawEvent(source="S", payload={"msg": "x" * 100}))  # oversize, offset -1
        engine.submit(RawEvent(source="S", payload={"msg": "ok2"}))  # offset 1

        engine.snapshot(snap_path)
        with open(snap_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["offset"] == 1
        assert data["event_count"] == 2
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)
