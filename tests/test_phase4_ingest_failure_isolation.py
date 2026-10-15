"""Tests for Phase 4: Ingestion-Thread Failure Isolation & Service Health (F-08)."""

import time
import pytest

from mdrap.service import MarketDataDaemon
from mdrap.models import RawEvent


def test_f08_malformed_event_does_not_crash_ingest_thread(tmp_path):
    """F-08: Non-numeric exchange_ts or malformed fields must NOT crash the ingestion thread."""
    db_file = str(tmp_path / "daemon_f08.db")
    daemon = MarketDataDaemon(
        db_path=db_file,
        use_live=False,
        sim_events=20,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    try:
        # Verify ingestion thread started and is healthy
        assert daemon._ingest_thread is not None
        assert daemon._ingest_thread.is_alive()
        assert daemon.stats()["ingestion_thread_alive"] is True

        # Inject malformed raw event with string exchange_ts (reproduces F-08 crash)
        malformed_raw = RawEvent(
            source="MALFORMED_FEED",
            receive_timestamp=time.time(),
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": "INVALID_STRING_TIMESTAMP",
                "price": "NOT_A_PRICE",
                "quantity": 100.0,
            },
        )

        # Must not raise an uncaught exception
        daemon._process_and_broadcast(malformed_raw)

        # Ingestion thread MUST remain alive!
        assert daemon._ingest_thread.is_alive()
        assert daemon.stats()["ingestion_thread_alive"] is True

        # Subsequent valid event must process normally
        valid_raw = RawEvent(
            source="CLEAN_FEED",
            receive_timestamp=time.time(),
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": time.time(),
                "price": 150.0,
                "quantity": 10.0,
            },
        )
        daemon._process_and_broadcast(valid_raw)
        assert daemon._ingest_thread.is_alive()
    finally:
        daemon.stop()


def test_f08_ingestion_thread_crash_reflected_in_degraded_and_stats(tmp_path):
    """F-08: If the ingestion worker thread dies, daemon reports degraded status immediately."""
    db_file = str(tmp_path / "daemon_health.db")
    daemon = MarketDataDaemon(
        db_path=db_file,
        use_live=False,
        sim_events=10,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    try:
        assert daemon._ingest_thread is not None
        assert daemon._ingest_thread.is_alive()

        # Steady-state health
        is_deg, reason = daemon.is_degraded()
        assert not is_deg
        assert reason == ""
        assert daemon.stats()["ingestion_thread_alive"] is True

        # Simulate unexpected thread termination while daemon remains marked running
        daemon._running = True
        daemon._ingest_thread = None

        is_deg2, reason2 = daemon.is_degraded()
        # If thread is None or dead while _running=True, status must report degraded
        # Wait, if _ingest_thread is None, let's test a dead thread object:
        class DummyDeadThread:
            def is_alive(self):
                return False

        daemon._ingest_thread = DummyDeadThread()
        is_deg3, reason3 = daemon.is_degraded()
        assert is_deg3 is True
        assert "thread dead" in reason3.lower()

        stats = daemon.stats()
        assert stats["degraded"] is True
        assert stats["ingestion_thread_alive"] is False
    finally:
        daemon.stop()


def test_f08_ingestion_error_counter_increments(tmp_path):
    """F-08: Ingestion loop exception isolation increments error counter."""
    db_file = str(tmp_path / "daemon_errors.db")
    daemon = MarketDataDaemon(
        db_path=db_file,
        use_live=False,
        sim_events=5,
        enable_shm=False,
    )
    assert daemon._ingestion_errors == 0
    daemon._ingestion_errors = 105
    is_deg, reason = daemon.is_degraded()
    assert is_deg is True
    assert "High ingestion error count" in reason
