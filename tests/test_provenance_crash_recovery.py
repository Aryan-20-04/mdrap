"""Crash recovery for the provenance retry WAL."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mdrap.provenance import ProvenanceTracker


def test_retry_is_recovered_after_process_termination(tmp_path):
    db_path = tmp_path / "crash-recovery.db"
    repo_root = Path(__file__).resolve().parents[1]
    child = r"""
import os, sqlite3, sys, time
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.provenance import ProvenanceTracker, create_provenance
class LockedStore:
    def write_provenance_batch(self, rows):
        raise sqlite3.OperationalError("database is locked")
path = sys.argv[1]
tracker = ProvenanceTracker(store=LockedStore(), db_path=path)
raw = RawEvent(source="CRASH_TEST", payload={"price": 42.0}, raw_id="raw-crash")
canon = CanonicalEvent(event_id="crash-event-1", instrument_id="XYZ",
    event_type=EventType.TRADE, exchange_timestamp=1.0, receive_timestamp=1.1,
    source="CRASH_TEST", price=42.0, quantity=1.0,
    quality_status=QualityStatus.VALID)
tracker.record(create_provenance(raw, canon))
os._exit(0)
"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    child_result = subprocess.run(
        [sys.executable, "-c", child, str(db_path)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert child_result.returncode == 0, child_result.stderr

    recovered = ProvenanceTracker(db_path=str(db_path))
    try:
        assert recovered.recovered_count == 1
        assert not recovered._pending_retry
    finally:
        recovered.close()

    verify = ProvenanceTracker(db_path=str(db_path))
    try:
        persisted = verify.get("crash-event-1")
        assert persisted is not None
        assert persisted.event_id == "crash-event-1"
        assert persisted.durability_status == "DURABLE_DISK"
    finally:
        verify.close()
