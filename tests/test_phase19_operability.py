"""Institutional Verification Suite for Phase 19: Production Operability Hardening.

Tests all remediations under Phase 19:
- OPS-02: Non-blocking asynchronous audit verification in prometheus.py preventing CPU DoS on scrapes.
- OPS-03: Timely file descriptor pruning and flushing across date partitions in archive.py.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.prometheus import PrometheusExporter
from mdrap.archive import RawArchive
from mdrap.models import RawEvent


def test_ops_02_prometheus_audit_verification_non_blocking():
    """Verify OPS-02: Scrapes complete with sub-millisecond latency even if audit check is slow."""
    # Simulate a slow store whose full Merkle scan takes 100ms
    mock_store = MagicMock()

    def slow_verify():
        time.sleep(0.1)
        return True, "Audit log verified", 1000

    mock_store.verify_audit_integrity = slow_verify

    state = MagicMock()
    state.start_time = time.time() - 100.0
    state.store = mock_store
    state.pipeline = None
    state.reliability = None
    state.kafka_sink = None

    exporter = PrometheusExporter(audit_check_interval_s=1.0)

    # Scrape 1: triggers async background check and returns immediately in microseconds
    t0 = time.perf_counter()
    metrics_text = exporter.render(state)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.05, f"render() blocked for {elapsed:.4f}s"
    assert "mdrap_audit_verified_status 1" in metrics_text

    # Scrape 2 immediately: does not spawn another worker and returns immediately
    t0 = time.perf_counter()
    metrics_text_2 = exporter.render(state)
    elapsed_2 = time.perf_counter() - t0
    assert elapsed_2 < 0.01

    # Wait for the async worker to complete
    time.sleep(0.15)
    metrics_text_3 = exporter.render(state)
    assert "mdrap_audit_verified_status 1" in metrics_text_3


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
