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
            exporter.record_health(
                {"status": "CONNECTED", "eps": 500.0 + i, "rss_mb": 42.1}
            )

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
            health_idxs = [
                json.loads(line)["data"]["idx"] for line in f if line.strip()
            ]
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
        for i in range(
            50
        ):  # 50 * 0.11s = 5.5s write work (> 5.0s legacy stop join timeout)
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
        exporter = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=60.0, queue_size=200
        )
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
        assert (
            res["submitted_records"] == res["accepted_records"] + res["dropped_records"]
        )
        assert res["failed_records"] == 2
        assert res["written_records"] == 8
        assert res["accepted_records"] == (
            res["written_records"] + res["failed_records"] + res["abandoned_on_timeout"]
        )
        assert res["flushed_records"] == res["written_records"]
        assert res["fsynced_records"] == res["written_records"]
        assert res["clean"] is False
        assert res["flush_succeeded"] is True
        assert res["fsync_succeeded"] is True
        assert res["close_succeeded"] is True


def test_exporter_enqueue_shutdown_race_deterministic(monkeypatch):
    """R3: Producer must not enqueue after stop() closes admission; accepted records cannot be orphaned."""
    import observer.exporter as exporter_mod

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=0.05, queue_size=100
        )
        exporter.start()
        exporter.record_event({"seq": 0})

        in_record_build = threading.Event()
        allow_enqueue_attempt = threading.Event()
        real_time = exporter_mod.time.time
        hook_state = {"armed": False}

        def paused_producer():
            exporter.record_event({"seq": "late"})

        def spy_time() -> float:
            if (
                hook_state["armed"]
                and threading.current_thread().name == "RaceProducer"
            ):
                in_record_build.set()
                assert allow_enqueue_attempt.wait(timeout=5.0)
            return real_time()

        monkeypatch.setattr(exporter_mod.time, "time", spy_time)
        hook_state["armed"] = True

        t = threading.Thread(target=paused_producer, name="RaceProducer")
        t.start()
        assert in_record_build.wait(timeout=5.0)

        # Shutdown closes admission and drains worker while RaceProducer passed outer running check
        stop_res = exporter.stop(timeout_s=5.0)
        assert stop_res["clean"] is True
        assert stop_res["worker_terminated"] is True

        # Now release RaceProducer into _enqueue_record(); lifecycle lock must reject enqueueing
        allow_enqueue_attempt.set()
        t.join(timeout=5.0)

        assert exporter._queue.qsize() == 0
        assert exporter.accepted_records == 1
        assert exporter.written_records == 1
        assert exporter.abandoned_on_timeout == 0

        with open(exporter.events_file, "r", encoding="utf-8") as f:
            rows = [json.loads(line)["data"]["seq"] for line in f if line.strip()]
        assert rows == [0]

        # Part B: Producer active inside _enqueue_record before stop() finishes -> written, never orphaned
        exporter2 = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=0.05, queue_size=500
        )
        exporter2.start()
        ready = threading.Event()
        stop_signal = threading.Event()

        def burst_producer():
            ready.set()
            i = 0
            while not stop_signal.is_set() and i < 200:
                exporter2.record_event({"seq": i})
                i += 1

        pt = threading.Thread(target=burst_producer)
        pt.start()
        assert ready.wait(timeout=5.0)
        res2 = exporter2.stop(timeout_s=5.0)
        stop_signal.set()
        pt.join(timeout=5.0)

        assert res2["clean"] is True
        assert res2["worker_terminated"] is True
        assert exporter2._queue.qsize() == 0
        assert (
            res2["submitted_records"]
            == res2["accepted_records"] + res2["dropped_records"]
        )
        assert res2["accepted_records"] == res2["written_records"]


def test_exporter_clean_shutdown_reporting_and_failure_separation(monkeypatch):
    """R4: Drained queue must not claim clean=True across open/write/flush/fsync/close failures."""
    real_open = builtins.open
    real_fsync = os.fsync

    # 1. File-open failure even with EMPTY queue
    with tempfile.TemporaryDirectory() as tmpdir:
        exp_open = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.05)
        exp_open.events_file = tmpdir  # directory -> open() raises OSError
        exp_open.start()
        res_open = exp_open.stop(timeout_s=5.0)
        assert res_open["queue_drained"] is True
        assert res_open["drained"] is True
        assert res_open["worker_terminated"] is True
        assert res_open["open_succeeded"] is False
        assert res_open["flush_succeeded"] is False
        assert res_open["close_succeeded"] is False
        assert res_open["flush_fsync_close_succeeded"] is False
        assert res_open["clean"] is False
        assert res_open["write_errors"] >= 1

    # 2. Write OSError on queued record
    with tempfile.TemporaryDirectory() as tmpdir:
        exp_write = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.05)

        def open_fail_write(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exp_write.events_file):
                return _FailingWriteStream(fp, fail_on_calls={1})
            return fp

        monkeypatch.setattr(builtins, "open", open_fail_write)
        exp_write.start()
        exp_write.record_event({"seq": 1})
        res_write = exp_write.stop(timeout_s=5.0)
        assert res_write["drained"] is True
        assert res_write["worker_terminated"] is True
        assert res_write["records_written_ok"] is False
        assert res_write["failed_records"] == 1
        assert res_write["flush_succeeded"] is True
        assert res_write["close_succeeded"] is True
        assert res_write["clean"] is False

    # 3. Flush OSError
    with tempfile.TemporaryDirectory() as tmpdir:
        exp_flush = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.05)

        def open_fail_flush(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exp_flush.events_file):
                return _FailingFlushStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", open_fail_flush)
        exp_flush.start()
        exp_flush.record_event({"seq": 1})
        res_flush = exp_flush.stop(timeout_s=5.0)
        assert res_flush["drained"] is True
        assert res_flush["worker_terminated"] is True
        assert res_flush["records_written_ok"] is True
        assert res_flush["flush_succeeded"] is False
        assert res_flush["flush_fsync_close_succeeded"] is False
        assert res_flush["clean"] is False

    # 4. Fsync OSError (with fsync_on_flush=True) even with EMPTY queue
    monkeypatch.setattr(builtins, "open", real_open)
    monkeypatch.setattr(
        os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync boom"))
    )
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            exp_fsync = ObserverExporter(
                output_dir=tmpdir, flush_interval_s=0.05, fsync_on_flush=True
            )
            exp_fsync.start()
            res_fsync = exp_fsync.stop(timeout_s=5.0)
            assert res_fsync["queue_drained"] is True
            assert res_fsync["drained"] is True
            assert res_fsync["worker_terminated"] is True
            assert res_fsync["flush_succeeded"] is True
            assert res_fsync["fsync_succeeded"] is False
            assert res_fsync["close_succeeded"] is True
            assert res_fsync["flush_fsync_close_succeeded"] is False
            assert res_fsync["clean"] is False
    finally:
        monkeypatch.setattr(os, "fsync", real_fsync)

    # 5. Close OSError even with EMPTY queue
    with tempfile.TemporaryDirectory() as tmpdir:
        exp_close = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.05)

        def open_fail_close(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exp_close.events_file):
                return _SlowFailingCloseStream(fp, fail_close=True)
            return fp

        monkeypatch.setattr(builtins, "open", open_fail_close)
        exp_close.start()
        res_close = exp_close.stop(timeout_s=5.0)
        assert res_close["queue_drained"] is True
        assert res_close["drained"] is True
        assert res_close["worker_terminated"] is True
        assert res_close["flush_succeeded"] is True
        assert res_close["close_succeeded"] is False
        assert res_close["flush_fsync_close_succeeded"] is False
        assert res_close["clean"] is False


def test_cli_warns_when_shutdown_unclean_even_with_empty_queue(monkeypatch, capsys):
    """R4 CLI: CLI must emit a warning to stderr when clean=False even if queue is empty."""
    from observer import cli as observer_cli

    real_open = builtins.open

    with tempfile.TemporaryDirectory() as tmpdir:

        def open_fail_events(file, mode="r", *args, **kwargs):
            if "events_" in os.path.basename(str(file)) and "a" in str(mode):
                raise OSError("Simulated events file open failure")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", open_fail_events)

        class _NoOpClient:
            def __init__(self, *args, **kwargs):
                self.reconnect_count = 0

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

        monkeypatch.setattr(observer_cli, "ObserverClient", _NoOpClient)
        monkeypatch.setattr(ObserverExporter, "record_health", lambda self, snap: None)

        rc = observer_cli.main(
            ["--export", tmpdir, "--refresh", "0.02", "--duration", "0.04", "--no-ui"]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "WARNING: exporter shutdown was not clean" in captured.err
        assert "clean=False" in captured.err
        assert "remaining_queue=0" in captured.err


def test_exporter_prevents_duplicate_workers_after_timeout(monkeypatch):
    """R5: Never spawn a second worker while previous worker is still alive after timeout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=60.0, queue_size=50
        )
        real_open = builtins.open
        worker_blocked = threading.Event()
        release_worker = threading.Event()

        class _BlockingOnceStream:
            def __init__(self, inner):
                self._inner = inner
                self._blocked = False

            def write(self, data: str) -> int:
                if not self._blocked:
                    self._blocked = True
                    worker_blocked.set()
                    assert release_worker.wait(timeout=5.0)
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
                return _BlockingOnceStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        # 1. Start and block the original worker
        exporter.start()
        original_worker = exporter._thread
        assert original_worker is not None
        exporter.record_event({"seq": 1})
        assert worker_blocked.wait(timeout=5.0)

        try:
            # 2. Cause shutdown timeout
            res_timeout = exporter.stop(timeout_s=0.05)
            assert res_timeout["timed_out"] is True
            assert res_timeout["worker_alive"] is True
            assert res_timeout["worker_terminated"] is False
            assert res_timeout["clean"] is False
            assert exporter._thread is original_worker

            # 3 & 4. Attempt restart while blocked worker is still alive -> no second worker starts
            exporter.start()
            assert exporter._thread is original_worker
            assert exporter.running is False
            worker_threads = [
                t for t in threading.enumerate() if t.name == "ObserverExporterWorker"
            ]
            assert len(worker_threads) == 1
            assert worker_threads[0] is original_worker
        finally:
            # 5. Release the original worker
            release_worker.set()

        # 6. Verify termination of the original worker
        original_worker.join(timeout=5.0)
        assert not original_worker.is_alive()
        res_drained = exporter.stop(timeout_s=5.0)
        assert res_drained["worker_terminated"] is True
        assert res_drained["worker_alive"] is False
        assert res_drained["clean"] is True

        # 7. Verify restart is safe after original worker terminated
        exporter.start()
        restarted_worker = exporter._thread
        assert restarted_worker is not None
        assert restarted_worker is not original_worker
        exporter.record_event({"seq": 2})
        res_final = exporter.stop(timeout_s=5.0)
        assert res_final["clean"] is True
        assert res_final["written_records"] == 2


def test_exporter_concurrent_start_blocked_during_shutdown_bookkeeping(monkeypatch):
    """A: Concurrent start() must not launch a worker after old worker exit while stop() bookkeeping runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=60.0, queue_size=50
        )
        real_open = builtins.open
        record_written = threading.Event()
        in_stop_bookkeeping = threading.Event()
        allow_bookkeeping_finish = threading.Event()

        class _SignalWriteStream:
            def __init__(self, inner):
                self._inner = inner

            def write(self, data: str) -> int:
                n = self._inner.write(data)
                record_written.set()
                return n

            def flush(self) -> None:
                self._inner.flush()

            def fileno(self) -> int:
                return self._inner.fileno()

            def close(self) -> None:
                self._inner.close()

        def patched_open(file, mode="r", *args, **kwargs):
            fp = real_open(file, mode, *args, **kwargs)
            if os.path.abspath(str(file)) == os.path.abspath(exporter.events_file):
                return _SignalWriteStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        exporter.start()
        original_worker = exporter._thread
        assert original_worker is not None
        exporter.record_event({"seq": 1})
        assert record_written.wait(timeout=5.0)

        real_qsize = exporter._queue.qsize
        stop_result_holder: dict[str, dict] = {}

        def spy_qsize() -> int:
            if (
                threading.current_thread().name == "StopBookkeepingThread"
                and not original_worker.is_alive()
            ):
                in_stop_bookkeeping.set()
                assert allow_bookkeeping_finish.wait(timeout=5.0)
            return real_qsize()

        monkeypatch.setattr(exporter._queue, "qsize", spy_qsize)

        def run_stop():
            stop_result_holder["result"] = exporter.stop(timeout_s=5.0)

        stop_thread = threading.Thread(target=run_stop, name="StopBookkeepingThread")
        stop_thread.start()
        assert in_stop_bookkeeping.wait(timeout=5.0)

        try:
            # Worker has already exited, while stop() is still inside post-join bookkeeping
            assert not original_worker.is_alive()
            assert exporter._stopping is True

            # Concurrent start() and subsequent event enqueue must be rejected without deadlocking
            exporter.start()
            exporter.record_event({"seq": "rejected-during-stopping"})

            assert exporter._thread is original_worker
            assert exporter.running is False
            assert exporter._lifecycle_epoch == 1
            assert not any(
                t.name == "ObserverExporterWorker" and t.is_alive()
                for t in threading.enumerate()
            )
        finally:
            allow_bookkeeping_finish.set()
            stop_thread.join(timeout=5.0)

        stop_res = stop_result_holder["result"]
        assert stop_res["clean"] is True
        assert stop_res["worker_terminated"] is True
        assert stop_res["remaining_queue"] == 0
        assert stop_res["written_records"] == 1
        assert stop_res["accepted_records"] == 1
        assert exporter.last_stop_result == stop_res
        assert exporter._stopping is False

        # Restart succeeds once previous lifecycle and bookkeeping are completely finalized
        exporter.start()
        restarted_worker = exporter._thread
        assert restarted_worker is not None
        assert restarted_worker is not original_worker
        assert exporter._lifecycle_epoch == 2
        exporter.record_event({"seq": 2})
        res_after_restart = exporter.stop(timeout_s=5.0)
        assert res_after_restart["clean"] is True
        assert res_after_restart["written_records"] == 2


def test_exporter_restart_after_timeout_deterministic(monkeypatch):
    """B: Timeout retains worker identity; restart fails while alive and succeeds after termination."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=60.0, queue_size=50
        )
        real_open = builtins.open
        in_blocked_write = threading.Event()
        release_write = threading.Event()

        class _ControlledBlockingStream:
            def __init__(self, inner):
                self._inner = inner
                self._first = True

            def write(self, data: str) -> int:
                if self._first:
                    self._first = False
                    in_blocked_write.set()
                    assert release_write.wait(timeout=5.0)
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
                return _ControlledBlockingStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        exporter.start()
        authoritative_worker = exporter._thread
        assert authoritative_worker is not None
        exporter.record_event({"seq": 1})
        assert in_blocked_write.wait(timeout=5.0)

        try:
            res_timeout = exporter.stop(timeout_s=0.05)
            assert res_timeout["incomplete_shutdown"] is True
            assert res_timeout["timed_out"] is True
            assert res_timeout["worker_alive"] is True
            assert res_timeout["clean"] is False
            assert exporter._thread is authoritative_worker

            exporter.start()
            assert exporter._thread is authoritative_worker
            assert exporter.running is False
            live_workers = [
                t
                for t in threading.enumerate()
                if t.name == "ObserverExporterWorker" and t.is_alive()
            ]
            assert live_workers == [authoritative_worker]
        finally:
            release_write.set()

        authoritative_worker.join(timeout=5.0)
        assert not authoritative_worker.is_alive()
        res_drained = exporter.stop(timeout_s=5.0)
        assert res_drained["clean"] is True
        assert res_drained["incomplete_shutdown"] is False
        assert res_drained["worker_terminated"] is True

        exporter.start()
        restarted_worker = exporter._thread
        assert restarted_worker is not None
        assert restarted_worker is not authoritative_worker
        exporter.record_event({"seq": 2})
        res_final = exporter.stop(timeout_s=5.0)
        assert res_final["clean"] is True
        assert res_final["written_records"] == 2


def test_exporter_concurrent_stops_deterministic(monkeypatch):
    """C: Concurrent stop() callers serialize coherently without double-counting or contradiction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = ObserverExporter(
            output_dir=tmpdir, flush_interval_s=60.0, queue_size=100
        )
        real_open = builtins.open
        write_paused = threading.Event()
        release_worker = threading.Event()

        class _GateFirstWriteStream:
            def __init__(self, inner):
                self._inner = inner
                self._first = True

            def write(self, data: str) -> int:
                if self._first:
                    self._first = False
                    write_paused.set()
                    assert release_worker.wait(timeout=5.0)
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
                return _GateFirstWriteStream(fp)
            return fp

        monkeypatch.setattr(builtins, "open", patched_open)

        exporter.start()
        worker = exporter._thread
        assert worker is not None

        for i in range(10):
            exporter.record_event({"seq": i})
        for i in range(4):
            exporter.record_health({"idx": i})
        assert write_paused.wait(timeout=5.0)

        join_gate_lock = threading.Lock()
        join_entered = 0
        both_joining = threading.Barrier(2, timeout=5.0)
        real_join = worker.join

        def synchronized_join(timeout=None):
            nonlocal join_entered
            with join_gate_lock:
                join_entered += 1
                if join_entered == 2:
                    release_worker.set()
            both_joining.wait()
            return real_join(timeout=timeout)

        monkeypatch.setattr(worker, "join", synchronized_join)

        results: dict[str, dict] = {}
        errors: list[BaseException] = []

        def caller(tag: str) -> None:
            try:
                results[tag] = exporter.stop(timeout_s=5.0)
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=caller, args=("c1",))
        t2 = threading.Thread(target=caller, args=("c2",))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors
        assert set(results) == {"c1", "c2"}
        r1 = results["c1"]
        r2 = results["c2"]
        assert r1 == r2
        assert r1["clean"] is True
        assert r1["drained"] is True
        assert r1["accepted_records"] == 14
        assert r1["written_records"] == 14
        assert r1["events_written"] == 10
        assert r1["health_written"] == 4
        assert r1["flushed_records"] == 14
        assert r1["remaining_queue"] == 0
        assert exporter._lifecycle_epoch == 1
        assert exporter._active_stops == 0
        assert exporter._stopping is False
        assert exporter.last_stop_result == r1
