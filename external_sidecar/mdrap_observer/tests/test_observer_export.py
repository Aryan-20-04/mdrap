"""Comprehensive tests for Observer JSONL exporter: concurrency, bounded queue drop counts, and I/O resilience."""

import builtins
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
    """Verify exporter handles unopenable paths (directory as file) cleanly with write_errors >= 1 and no handle leaks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Case 1: events_file points to an existing directory -> open() raises OSError (PermissionError / IsADirectoryError)
        bad_events_dir = os.path.join(tmpdir, "events_dir")
        os.makedirs(bad_events_dir)

        exporter = ObserverExporter(output_dir=tmpdir)
        exporter.events_file = bad_events_dir
        exporter.start()

        for i in range(5):
            exporter.record_event({"seq": i})

        time.sleep(0.15)
        exporter.stop()
        assert exporter.write_errors >= 1
        assert exporter.events_written == 0
        assert not (exporter._thread and exporter._thread.is_alive())

        # Case 2: events_file opens normally, health_file points to an existing directory -> events_fp must be closed
        bad_health_dir = os.path.join(tmpdir, "health_dir")
        os.makedirs(bad_health_dir)
        events_ok_path = os.path.join(tmpdir, "events_partial.jsonl")

        exporter2 = ObserverExporter(output_dir=tmpdir)
        exporter2.events_file = events_ok_path
        exporter2.health_file = bad_health_dir
        exporter2.start()

        time.sleep(0.15)
        exporter2.stop()
        assert exporter2.write_errors >= 1
        assert not (exporter2._thread and exporter2._thread.is_alive())
        # On Windows, an unclosed file handle prevents deleting events_ok_path
        os.remove(events_ok_path)


class _FailingWriteStream:
    """File proxy that raises OSError on selected write() calls."""

    def __init__(self, inner, fail_on_calls: set[int]):
        self._inner = inner
        self._fail_on_calls = fail_on_calls
        self._write_calls = 0

    def write(self, data: str) -> int:
        self._write_calls += 1
        if self._write_calls in self._fail_on_calls:
            raise OSError(f"Simulated write OSError on call {self._write_calls}")
        return self._inner.write(data)

    def flush(self) -> None:
        self._inner.flush()

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


class _FailingFlushStream:
    """File proxy that raises OSError on flush()."""

    def __init__(self, inner):
        self._inner = inner

    def write(self, data: str) -> int:
        return self._inner.write(data)

    def flush(self) -> None:
        raise OSError("Simulated flush OSError")

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


def test_exporter_midstream_write_oserror(monkeypatch):
    """Mid-stream write OSError increments write_errors and loop continues processing subsequent records safely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.05)
        real_open = builtins.open

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _FailingWriteStream(fp, fail_on_calls={2, 3})
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        exporter.start()
        for i in range(6):
            exporter.record_event({"seq": i})
        for i in range(2):
            exporter.record_health({"idx": i})

        time.sleep(0.15)
        exporter.stop()

        assert exporter.write_errors == 2
        assert exporter.events_written == 4
        assert exporter.health_written == 2

        with real_open(exporter.events_file, "r", encoding="utf-8") as f:
            event_seqs = [json.loads(line)["data"]["seq"] for line in f if line.strip()]
        assert event_seqs == [0, 3, 4, 5]

        with real_open(exporter.health_file, "r", encoding="utf-8") as f:
            health_idxs = [json.loads(line)["data"]["idx"] for line in f if line.strip()]
        assert health_idxs == [0, 1]


def test_exporter_midstream_flush_oserror(monkeypatch):
    """Mid-stream flush OSError increments write_errors and closes cleanly without uncaught exceptions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.05)
        real_open = builtins.open

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _FailingFlushStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        exporter.start()
        exporter.record_event({"seq": 1})
        time.sleep(0.15)
        exporter.stop()

        assert exporter.write_errors >= 1
        assert exporter.events_written == 1
        assert not (exporter._thread and exporter._thread.is_alive())


def test_exporter_fsync_on_flush(monkeypatch):
    """Verify fsync_on_flush=True invokes os.fsync on flush and final close while persisting valid JSONL."""
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir,
            flush_interval_s=0.05,
            fsync_on_flush=True,
        )
        exporter.start()
        for i in range(5):
            exporter.record_event({"seq": i})
        exporter.record_health({"status": "CONNECTED"})

        time.sleep(0.15)
        exporter.stop()

        assert exporter.write_errors == 0
        assert exporter.events_written == 5
        assert exporter.health_written == 1
        # At least events + health fsynced on periodic flush and/or close
        assert len(fsync_calls) >= 2

        with open(exporter.events_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert [row["data"]["seq"] for row in lines] == [0, 1, 2, 3, 4]


def test_exporter_clean_shutdown_drain():
    """Verify stop() drains all queued records with zero missing, duplicate, or invalid JSONL lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir,
            flush_interval_s=60.0,
            queue_size=1000,
        )
        exporter.start()

        for i in range(200):
            exporter.record_event({"seq": i})
        for i in range(20):
            exporter.record_health({"idx": i})

        exporter.stop()

        assert exporter.events_written == 200
        assert exporter.health_written == 20
        assert exporter.dropped_records == 0
        assert exporter.write_errors == 0

        with open(exporter.events_file, "r", encoding="utf-8") as f:
            event_lines = [json.loads(line) for line in f if line.strip()]
        assert [row["data"]["seq"] for row in event_lines] == list(range(200))

        with open(exporter.health_file, "r", encoding="utf-8") as f:
            health_lines = [json.loads(line) for line in f if line.strip()]
        assert [row["data"]["idx"] for row in health_lines] == list(range(20))


def test_exporter_rejects_nonpositive_queue_size():
    """queue_size <= 0 or bool/non-int must raise ValueError rather than creating unbounded queue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for bad in (0, -1, -100, False, 1.5):
            with pytest.raises(ValueError, match="queue_size"):
                ObserverExporter(output_dir=tmpdir, queue_size=bad)


class _SlowFailingCloseStream:
    """File proxy simulating slow writes and optional fsync/close OSError."""

    def __init__(self, inner, write_delay_s: float = 0.0, fail_close: bool = False):
        self._inner = inner
        self._write_delay_s = write_delay_s
        self._fail_close = fail_close

    def write(self, data: str) -> int:
        if self._write_delay_s > 0.0:
            time.sleep(self._write_delay_s)
        return self._inner.write(data)

    def flush(self) -> None:
        self._inner.flush()

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()
        if self._fail_close:
            raise OSError("Simulated close OSError")


def test_exporter_slow_fsync_exceeding_5s_drains_completely(monkeypatch):
    """Default stop() must drain work exceeding the historical 5s timeout with exact accounting."""
    real_fsync = os.fsync
    fsync_calls = 0

    def slow_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        time.sleep(0.05)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", slow_fsync)

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir,
            flush_interval_s=0.05,
            queue_size=500,
            fsync_on_flush=True,
        )
        real_open = builtins.open

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _SlowFailingCloseStream(fp, write_delay_s=0.11)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        exporter.start()
        for i in range(50):  # 50 * 0.11s = 5.5s write work (> 5.0s legacy stop join timeout)
            exporter.record_event({"seq": i})
        exporter.record_health({"status": "CONNECTED"})

        t0 = time.monotonic()
        res = exporter.stop()  # default timeout_s=30.0
        elapsed = time.monotonic() - t0

        assert elapsed >= 5.0
        assert res["clean"] is True
        assert res["drained"] is True
        assert res["timed_out"] is False
        assert res["remaining_queue"] == 0
        assert res["abandoned_on_timeout"] == 0
        assert res["events_written"] == 50
        assert res["health_written"] == 1
        assert res["written_records"] == 51
        assert res["flushed_records"] == 51
        assert res["fsynced_records"] == 51
        assert res["failed_records"] == 0
        assert res["write_errors"] == 0
        assert fsync_calls >= 2

        with real_open(exporter.events_file, "r", encoding="utf-8") as f:
            seqs = [json.loads(line)["data"]["seq"] for line in f if line.strip()]
        assert seqs == list(range(50))


def test_exporter_stop_timeout_reports_incomplete_drain(monkeypatch):
    """If stop(timeout_s=...) expires early, return explicit incomplete status or raise TimeoutError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(output_dir=tmpdir, flush_interval_s=60.0, queue_size=200)
        real_open = builtins.open
        first_started = threading.Event()
        release_blocker = threading.Event()

        class _BlockingStream:
            def __init__(self, inner):
                self._inner = inner

            def write(self, data: str) -> int:
                first_started.set()
                release_blocker.wait(timeout=2.0)
                return self._inner.write(data)

            def flush(self) -> None:
                self._inner.flush()

            def fileno(self) -> int:
                return self._inner.fileno()

            def close(self) -> None:
                self._inner.close()

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _BlockingStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)
        exporter.start()
        for i in range(15):
            exporter.record_event({"seq": i})
        assert first_started.wait(timeout=1.5)

        try:
            res = exporter.stop(timeout_s=0.1)
            assert res["clean"] is False
            assert res["drained"] is False
            assert res["timed_out"] is True
            assert res["worker_alive"] is True
            assert res["remaining_queue"] >= 1
            assert res["abandoned_on_timeout"] == res["remaining_queue"]
            assert exporter.incomplete_shutdown is True
            assert exporter.last_stop_result is not None
            assert exporter.last_stop_result["timed_out"] is True

            # Calling stop with raise_on_timeout=True while still blocked raises TimeoutError
            with pytest.raises(TimeoutError, match="did not drain"):
                exporter.stop(timeout_s=0.05, raise_on_timeout=True)
        finally:
            release_blocker.set()
            final = exporter.stop(timeout_s=5.0)
            assert final["drained"] is True


def test_exporter_fsync_and_close_failures_truthful_accounting(monkeypatch):
    """fsync/close OSErrors must increment write_errors without advancing fsynced counters."""
    def failing_fsync(fd: int) -> None:
        raise OSError("Simulated fsync OSError")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir,
            flush_interval_s=0.05,
            fsync_on_flush=True,
        )
        real_open = builtins.open

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _SlowFailingCloseStream(fp, fail_close=True)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)
        exporter.start()
        for i in range(8):
            exporter.record_event({"seq": i})
        res = exporter.stop()

        assert res["drained"] is True
        assert res["events_written"] == 8
        assert res["events_flushed"] == 8
        assert res["events_fsynced"] == 0
        assert res["fsynced_records"] == 0
        assert res["write_errors"] >= 2  # fsync + close errors


def test_exporter_full_accounting_reconciliation(monkeypatch):
    """submitted == accepted + dropped, accepted == written + failed + abandoned_on_timeout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir,
            flush_interval_s=0.05,
            queue_size=10,
            fsync_on_flush=True,
        )
        real_open = builtins.open

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _FailingWriteStream(fp, fail_on_calls={2, 4})
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)
        # Start writer, or test overflow by pausing writes briefly via write error / full queue
        exporter.running = True
        for i in range(12):  # 10 fit in queue, 2 dropped
            exporter.record_event({"seq": i})
        for i in range(5):  # queue full -> all 5 dropped
            exporter.record_health({"idx": i})

        assert exporter.dropped_records == 7
        exporter.start()
        res = exporter.stop(timeout_s=5.0)

        assert res["drained"] is True
        assert res["submitted_records"] == 17
        assert res["accepted_records"] == 10
        assert res["dropped_records"] == 7
        assert res["submitted_records"] == res["accepted_records"] + res["dropped_records"]
        assert res["failed_records"] == 2
        assert res["written_records"] == 8
        assert res["accepted_records"] == (
            res["written_records"] + res["failed_records"] + res["abandoned_on_timeout"]
        )
        assert res["flushed_records"] == res["written_records"]
        assert res["fsynced_records"] == res["written_records"]
