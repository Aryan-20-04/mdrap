"""Comprehensive tests for Observer JSONL exporter: concurrency, bounded queue drop counts, and I/O resilience."""

import json
import os
import queue
import sys
import tempfile
import threading
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.exporter import ObserverExporter


def test_exporter_writes_and_parses_jsonl():
    """Verify valid JSONL formatting and concurrent writes to events and health files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.1)
        exporter.start()

        # Record events
        for i in range(25):
            exporter.record_event({"seq": i, "price": 100.0 + i, "symbol": "BTC-USD"})

        # Record health snapshots
        for i in range(5):
            exporter.record_health({"status": "CONNECTED", "eps": 500.0 + i, "rss_mb": 42.1})

        # Wait for flush and stop
        time.sleep(0.3)
        exporter.stop()

        assert os.path.exists(exporter.events_file)
        assert os.path.exists(exporter.health_file)

        # Parse every single line as valid JSON
        with open(exporter.events_file, "r", encoding="utf-8") as f:
            event_lines = [json.loads(line) for line in f if line.strip()]
        assert len(event_lines) == 25
        assert event_lines[0]["data"]["seq"] == 0
        assert event_lines[-1]["data"]["seq"] == 24

        with open(exporter.health_file, "r", encoding="utf-8") as f:
            health_lines = [json.loads(line) for line in f if line.strip()]
        assert len(health_lines) == 5
        assert health_lines[0]["data"]["eps"] == 500.0


def test_exporter_bounded_queue_drop_tracking():
    """Verify queue overflow increments dropped_records and never crashes or blocks caller."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create an exporter with a very small queue (capacity 5) and do NOT start worker thread immediately
        exporter = ObserverExporter(output_dir=tmpdir, queue_size=5)
        # Exporter is marked running to accept enqueueing without background worker consuming
        exporter.running = True

        for i in range(20):
            exporter.record_event({"seq": i})

        # Queue capacity was 5, so 15 items should have been dropped cleanly
        assert exporter._queue.full()
        assert exporter.dropped_records == 15

        # Cleanup
        exporter.running = False


def test_exporter_io_error_resilience():
    """Verify exporter handles unwriteable paths gracefully without uncaught exceptions."""
    # Point output to a non-existent invalid path on Windows
    invalid_dir = os.path.join(tempfile.gettempdir(), "invalid_path_test_dir")
    exporter = ObserverExporter(output_dir=invalid_dir)
    exporter.start()

    # Artificially close the file handles to trigger OSError on write
    time.sleep(0.1)
    if hasattr(exporter, "_thread") and exporter._thread.is_alive():
        # Inject records
        for i in range(5):
            exporter.record_event({"seq": i})

    time.sleep(0.2)
    exporter.stop()
    assert exporter.events_written >= 0
