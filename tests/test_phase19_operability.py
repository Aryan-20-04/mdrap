"""Institutional Verification Suite for Phase 19: Production Operability Hardening.

Tests all remediations under Phase 19:
- OPS-02: Non-blocking asynchronous audit verification in prometheus.py preventing CPU DoS on scrapes.
- OPS-03: Timely file descriptor pruning and flushing across date partitions in archive.py.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.prometheus import PrometheusExporter
from mdrap.archive import RawArchive
from mdrap.models import RawEvent


def test_ops_02_prometheus_audit_verification_non_blocking():
    """Verify OPS-02: render() dispatches slow audit checks to prometheus-audit-worker without blocking."""
    verify_started = threading.Event()
    release_verify = threading.Event()
    verify_finished = threading.Event()
    worker_threads: list[str] = []

    def blocked_verify():
        worker_threads.append(threading.current_thread().name)
        verify_started.set()
        if not release_verify.wait(timeout=10.0):
            raise TimeoutError("Safety watchdog expired waiting to release mock audit check")
        verify_finished.set()
        return True, "Audit log verified", 1000

    store = SimpleNamespace(conflicts=0, verify_audit_integrity=blocked_verify)
    state = SimpleNamespace(
        start_time=time.time() - 100.0,
        store=store,
        engine=None,
        pipeline=None,
        watchdog=None,
        reliability=None,
        kafka_sink=None,
        alert_engine=None,
        quality_engine=None,
    )

    exporter = PrometheusExporter(audit_check_interval_s=1.0)
    caller_result: dict[str, object] = {}
    caller_done = threading.Event()

    def invoke_render() -> None:
        try:
            caller_result["text"] = exporter.render(state)
        except BaseException as exc:  # pragma: no cover - surfaced below
            caller_result["exc"] = exc
        finally:
            caller_done.set()

    caller_thread = threading.Thread(
        target=invoke_render,
        name="prometheus-scrape-caller",
        daemon=True,
    )

    try:
        # Scrape 1: submits work to the pre-started worker and returns while verification stays blocked
        caller_thread.start()
        assert verify_started.wait(timeout=5.0), "Background audit worker did not start"
        assert caller_done.wait(timeout=5.0), "render() caller blocked while verification waited"
        assert "exc" not in caller_result, f"Caller raised: {caller_result.get('exc')}"
        assert not verify_finished.is_set(), "Caller waited until slow audit completed"
        assert worker_threads == ["prometheus-audit-worker"]
        assert "mdrap_audit_verified_status 1" in str(caller_result["text"])

        # Scrape 2 immediately: does not spawn duplicate checks and completes while worker still waits
        metrics_text_2 = exporter.render(state)
        assert not verify_finished.is_set()
        assert len(worker_threads) == 1
        assert "mdrap_audit_verified_status 1" in metrics_text_2

        # Release background worker and verify clean completion
        release_verify.set()
        assert verify_finished.wait(timeout=5.0), "Background audit worker did not finish"
        metrics_text_3 = exporter.render(state)
        assert "mdrap_audit_verified_status 1" in metrics_text_3
        assert len(worker_threads) == 1
    finally:
        release_verify.set()
        exporter.close()
        caller_thread.join(timeout=5.0)
        exporter._audit_worker_thread.join(timeout=5.0)
        assert not exporter._audit_worker_thread.is_alive()


def test_ops_02_prometheus_audit_verification_tamper_detection():
    """Verify OPS-02: Tampered audit log is accurately reflected in gauge."""
    mock_store = MagicMock()
    mock_store.verify_audit_integrity.return_value = (
        False,
        "Hash mismatch at entry 42",
        50,
    )

    state = MagicMock()
    state.start_time = time.time() - 100.0
    state.store = mock_store
    state.pipeline = None
    state.reliability = None
    state.kafka_sink = None

    exporter = PrometheusExporter()
    status = exporter.verify_audit_sync(mock_store)
    assert status == 0

    metrics_text = exporter.render(state)
    assert "mdrap_audit_verified_status 0" in metrics_text


def test_ops_03_raw_archive_fd_cleanup():
    """Verify OPS-03: RawArchive cleans up file descriptors across date boundaries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        archive = RawArchive(base_dir=tmpdir, buffer_size=1)

        # Write 20 separate days to exceed the 16 open handle pruning limit
        base_ts = 1700000000.0  # Unix timestamp
        for i in range(20):
            ts = base_ts + (i * 86400.0)
            raw = RawEvent(
                source=f"FEED_{i}",
                payload={"instrument": "BTC/USD", "price": 50000.0 + i},
                receive_timestamp=ts,
                raw_id=f"r_{i}",
            )
            archive.write(raw)

        # Buffer size is 1, so every write was flushed immediately
        # Pruning kicks in when handles > 16, pruning inactive handles
        assert len(archive._file_handles) <= 16

        # Clean close
        archive.close()
        assert len(archive._file_handles) == 0
