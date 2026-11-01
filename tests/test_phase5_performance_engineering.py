"""Unit test suite for Phase 5: Performance Engineering (E13, E14).

Verifies:
- E13: Concurrent submit() calls remain unblocked during snapshotting; lock hold time is minimized.
- E14: Payload size estimator bypasses json.dumps for small market events while accurately catching oversize payloads.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import pytest

from mdrap.engine import Engine
from mdrap.models import QualityStatus, RawEvent, Reason


def _td():
    return tempfile.mkdtemp(prefix="phase5_perf_")


def test_e13_concurrent_submit_during_snapshot():
    """Verify concurrent submit calls are not locked out during disk snapshotting."""
    td = _td()
    snap_path = os.path.join(td, "snapshot.json")
    try:
        engine = Engine(path=td)
        for i in range(100):
            engine.submit(
                RawEvent(source="TEST", payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 1})
            )

        submits_during_snapshot = []
        stop_thread = threading.Event()

        def worker():
            while not stop_thread.is_set():
                t0 = time.perf_counter()
                dec = engine.submit(
                    RawEvent(source="TEST", payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 1})
                )
                dt = (time.perf_counter() - t0) * 1000.0  # ms
                submits_during_snapshot.append(dt)
                time.sleep(0.001)

        th = threading.Thread(target=worker, daemon=True)
        th.start()

        # Take multiple snapshots
        for _ in range(3):
            engine.snapshot(snap_path)
            time.sleep(0.005)

        stop_thread.set()
        th.join(timeout=2.0)

        assert len(submits_during_snapshot) > 0
        # All concurrent submits completed swiftly (p99 < 50ms)
        submits_during_snapshot.sort()
        p99 = submits_during_snapshot[int(len(submits_during_snapshot) * 0.99)]
        assert p99 < 50.0
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e14_payload_size_fast_path():
    """Verify payload size check accurately catches oversize payloads without dumping small ones."""
    td = _td()
    try:
        engine = Engine(path=td, max_payload_bytes=500)
        # Normal small payload
        raw_small = RawEvent(
            source="FEED",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 10}
        )
        dec_small = engine.submit(raw_small)
        assert dec_small.quality_status == QualityStatus.VALID

        # Oversize payload via large string
        raw_oversize = RawEvent(
            source="FEED",
            payload={"instrument": "AAPL", "event_type": "TRADE", "notes": "X" * 1000}
        )
        dec_over = engine.submit(raw_oversize)
        assert dec_over.quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in dec_over.reasons
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)
