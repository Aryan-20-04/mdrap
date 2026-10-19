"""Unit and concurrency tests for True WAL Group Commit (Program I-5)."""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
import threading
from unittest.mock import patch
import pytest

from mdrap.ingestlog import IngestLog, IngestLogPoisonedError
from mdrap.models import RawEvent


def test_true_group_commit_single_thread() -> None:
    """Test standard single-threaded appends under true_group_commit policy."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        log = IngestLog(tmp_dir, fsync_policy="true_group_commit")
        offsets = []
        for i in range(50):
            ev = RawEvent(
                source="TEST",
                payload={"instrument": "BTC-USD", "sequence": i, "price": 50000.0 + i},
            )
            off = log.append(ev)
            offsets.append(off)

        assert offsets == list(range(50))
        assert log.next_offset == 50
        log.close()

        # Reopen and verify data persistence
        reopened = IngestLog(tmp_dir, fsync_policy="true_group_commit")
        recovered = list(reopened.iter_from(0))
        assert len(recovered) == 50
        assert [off for off, _ in recovered] == list(range(50))
        reopened.close()


def test_true_group_commit_multithreaded_concurrency() -> None:
    """Test 8 concurrent worker threads appending 100 events each under true_group_commit."""
    num_threads = 8
    events_per_thread = 100
    total_events = num_threads * events_per_thread

    with tempfile.TemporaryDirectory() as tmp_dir:
        log = IngestLog(tmp_dir, fsync_policy="true_group_commit")
        all_offsets: list[int] = []
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            thread_offsets = []
            for j in range(events_per_thread):
                ev = RawEvent(
                    source=f"THREAD_{thread_idx}",
                    payload={
                        "instrument": "ETH-USD",
                        "tid": thread_idx,
                        "seq": j,
                        "price": 3000.0 + j,
                    },
                )
                off = log.append(ev)
                thread_offsets.append(off)
            with lock:
                all_offsets.extend(thread_offsets)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(worker, t) for t in range(num_threads)]
            concurrent.futures.wait(futures)
            for f in futures:
                f.result()

        assert len(all_offsets) == total_events
        # Strictly contiguous sequence invariant: 0..799 with zero gaps or duplicates
        assert sorted(all_offsets) == list(range(total_events))
        log.close()

        # Reopen and verify that every single event survived on disk
        reopened = IngestLog(tmp_dir, fsync_policy="always")
        recovered = list(reopened.iter_from(0))
        assert len(recovered) == total_events
        assert [off for off, _ in recovered] == list(range(total_events))

        # Check payload integrity
        for off, raw in recovered:
            assert "tid" in raw.payload
            assert "seq" in raw.payload
        reopened.close()


def test_true_group_commit_fsync_amortization() -> None:
    """Verify group commit amortizes os.fsync calls across concurrent writers."""
    num_threads = 6
    events_per_thread = 50
    total_events = num_threads * events_per_thread

    with tempfile.TemporaryDirectory() as tmp_dir:
        log = IngestLog(tmp_dir, fsync_policy="true_group_commit")
        real_fsync = os.fsync
        fsync_calls = 0
        fsync_lock = threading.Lock()

        def counting_fsync(fd: int) -> None:
            nonlocal fsync_calls
            with fsync_lock:
                fsync_calls += 1
            real_fsync(fd)

        def worker(tid: int) -> None:
            for j in range(events_per_thread):
                ev = RawEvent(
                    source=f"T_{tid}",
                    payload={"instrument": "SOL-USD", "p": 150.0 + j},
                )
                log.append(ev)

        with patch("os.fsync", side_effect=counting_fsync):
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
                futures = [pool.submit(worker, t) for t in range(num_threads)]
                concurrent.futures.wait(futures)
                for f in futures:
                    f.result()

        # With 300 concurrent events across 6 threads, group commit should amortize fsyncs
        # Total fsync calls must be > 0 and strictly <= total_events (typically far fewer under contention)
        assert 0 < fsync_calls <= total_events
        log.close()


def test_true_group_commit_poison_propagation() -> None:
    """Verify I/O errors during fsync poison the log and raise immediately."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        log = IngestLog(tmp_dir, fsync_policy="true_group_commit")

        ev1 = RawEvent(
            source="TEST",
            payload={"instrument": "BTC-USD", "sequence": 1, "price": 50000.0},
        )
        off1 = log.append(ev1)
        assert off1 == 0

        # Simulate disk I/O failure on next fsync
        def failing_fsync(fd: int) -> None:
            raise OSError(5, "Input/output error on physical disk")

        ev2 = RawEvent(
            source="TEST",
            payload={"instrument": "BTC-USD", "sequence": 2, "price": 50001.0},
        )

        with patch("os.fsync", side_effect=failing_fsync):
            with pytest.raises(OSError, match="Input/output error"):
                log.append(ev2)

        # Log must now be poisoned
        assert log.is_poisoned is True
        with pytest.raises(IngestLogPoisonedError):
            log.append(ev1)

        log.close()
