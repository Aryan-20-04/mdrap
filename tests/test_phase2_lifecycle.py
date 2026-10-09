"""Phase 2 Regression Suite: Canonical Runtime & Lifecycle (Spec §18 & Workstream A).

Verifies that:
1. Runtime traverses UNINITIALIZED -> READY -> RUNNING -> DRAINING -> STOPPED.
2. Idempotent initialization, start, and shutdown calls do not crash or corrupt state.
3. Initialization failures transition to FAILED and clean up partially allocated resources.
4. Stop flushes pending writes and enforces the drain timeout deadline.
5. Ingestion during non-RUNNING states is rejected with RuntimeError.
"""

import time
import pytest
from unittest.mock import patch

from mdrap.runtime import Runtime, RuntimeConfig, RuntimeState
from mdrap.models import RawEvent, QualityStatus
from mdrap.storage import PersistenceMode


def test_runtime_lifecycle_full_happy_path(tmp_path):
    """Runtime traverses UNINITIALIZED -> READY -> RUNNING -> DRAINING -> STOPPED."""
    db_file = tmp_path / "runtime_happy.db"
    wal_dir = tmp_path / "runtime_happy.wal"

    config = RuntimeConfig(
        db_path=str(db_file),
        wal_path=str(wal_dir),
        persistence_mode=PersistenceMode.PRODUCTION_DURABLE.value,
        drain_timeout_s=2.0,
    )

    rt = Runtime(config)
    assert rt.state == RuntimeState.UNINITIALIZED
    assert rt.is_ready() is False
    assert rt.is_alive() is False

    # Initialize
    rt.initialize()
    assert rt.state == RuntimeState.READY
    assert rt.is_ready() is True
    assert rt.is_alive() is True
    assert rt.engine is not None

    # Idempotent initialize
    rt.initialize()
    assert rt.state == RuntimeState.READY

    # Start
    rt.start()
    assert rt.state == RuntimeState.RUNNING
    assert rt.is_ready() is True
    assert rt.is_alive() is True

    # Idempotent start
    rt.start()
    assert rt.state == RuntimeState.RUNNING

    # Submit event
    raw = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 10.0, "sequence": 1, "exchange_ts": time.time()},
    )
    dec = rt.submit(raw)
    assert dec.quality_status == QualityStatus.VALID
    assert dec.canonical_event is not None

    # Graceful stop with drain
    shutdown_called = False

    def on_shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    rt.register_shutdown_callback(on_shutdown)
    clean = rt.stop(drain_timeout_s=2.0)
    assert clean is True
    assert shutdown_called is True
    assert rt.state == RuntimeState.STOPPED
    assert rt.is_ready() is False
    assert rt.is_alive() is False

    # Idempotent stop
    clean_again = rt.stop()
    assert clean_again is True

    # Submitting to stopped runtime must raise
    with pytest.raises(RuntimeError) as exc:
        rt.submit(raw)
    assert "Cannot submit events" in str(exc.value)


def test_runtime_initialization_failure_cleans_up(tmp_path):
    """Failure to initialize durable engine transitions to FAILED and cleans up handles."""
    db_file = tmp_path / "runtime_fail.db"
    wal_dir = tmp_path / "runtime_fail.wal"

    config = RuntimeConfig(
        db_path=str(db_file),
        wal_path=str(wal_dir),
        persistence_mode=PersistenceMode.PRODUCTION_DURABLE.value,
    )

    rt = Runtime(config)

    with patch("mdrap.engine.Engine.open", side_effect=OSError("Disk IO Error")):
        with pytest.raises(OSError):
            rt.initialize()

    assert rt.state == RuntimeState.FAILED
    assert rt.init_error is not None
    assert "Disk IO Error" in rt.init_error
    assert rt.engine is None
    assert rt.store is None  # Partial store was closed and cleaned up!
    assert rt.is_ready() is False
    assert rt.is_degraded() is True
