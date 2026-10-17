"""Tests for Phase 3: WAL Batch Atomicity, Poison Exception Safety, and Durability Contract (F-04, F-05, F-06)."""

import os
import unittest.mock as mock
import pytest

from mdrap.ingestlog import (
    IngestLog,
    IngestLogError,
    IngestLogCorruptError,
    IngestLogPoisonedError,
)
from mdrap.models import RawEvent


def test_f05_poisoned_error_defined(tmp_path):
    """F-05: IngestLogPoisonedError is defined and raised when log is poisoned."""
    assert issubclass(IngestLogPoisonedError, IngestLogError)

    log_dir = str(tmp_path / "poison_test")
    log = IngestLog(log_dir)
    try:
        # Simulate unrecoverable I/O rollback failure setting _is_poisoned
        log._is_poisoned = True

        raw = RawEvent(source="TEST", payload={"instrument": "AAPL", "price": 100.0})

        # Both append and append_batch must raise IngestLogPoisonedError, not NameError
        with pytest.raises(IngestLogPoisonedError) as exc_info:
            log.append(raw)
        assert "poisoned" in str(exc_info.value).lower()

        with pytest.raises(IngestLogPoisonedError) as exc_info2:
            log.append_batch([raw])
        assert "poisoned" in str(exc_info2.value).lower()
    finally:
        log.close()


def test_f04_unserializable_event_in_batch_aborts_without_orphan_frames(tmp_path):
    """F-04 Repro: Unserializable payload in batch raises exception with ZERO writes and zero offset advancement."""
    log_dir = str(tmp_path / "f04_abort_test")
    log = IngestLog(log_dir)
    try:
        e1 = RawEvent(source="A", receive_timestamp=1.0, payload={"i": "X", "val": 1})
        e2 = RawEvent(
            source="A", receive_timestamp=1.0, payload={"i": "X", "val": object()}
        )  # Unserializable

        with pytest.raises(TypeError):
            log.append_batch([e1, e2])

        # State must remain 0: e1 was NOT persisted, offset NOT advanced
        assert log.next_offset == 0
        assert list(log.iter_from(0)) == []
    finally:
        log.close()

    # Reopen to verify disk is clean
    log2 = IngestLog(log_dir)
    try:
        assert log2.next_offset == 0
        assert list(log2.iter_from(0)) == []

        # Subsequent valid batch proceeds from offset 0 with zero duplicate offsets
        valid_offsets = log2.append_batch(
            [
                RawEvent(
                    source="A", receive_timestamp=1.0, payload={"i": "X", "val": 10}
                ),
                RawEvent(
                    source="A", receive_timestamp=1.0, payload={"i": "X", "val": 20}
                ),
            ]
        )
        assert valid_offsets == [0, 1]
        assert log2.next_offset == 2
        recs = list(log2.iter_from(0))
        assert len(recs) == 2
        assert recs[0][1].payload["val"] == 10
        assert recs[1][1].payload["val"] == 20
    finally:
        log2.close()


def test_f04_io_failure_during_batch_rolls_back_cleanly_without_orphans(tmp_path):
    """F-04: I/O failure midway through batch append cleanly truncates and allows retry without duplicates."""
    log_dir = str(tmp_path / "f04_io_rollback")
    # Small max segment to trigger rotation
    log = IngestLog(log_dir, max_segment_bytes=500, fsync_policy="always")

    try:
        # Pre-fill segment 0 with 2 events
        initial_offsets = log.append_batch(
            [
                RawEvent(
                    source="A", receive_timestamp=1.0, payload={"i": "A", "val": 1}
                ),
                RawEvent(
                    source="A", receive_timestamp=1.0, payload={"i": "A", "val": 2}
                ),
            ]
        )
        assert initial_offsets == [0, 1]
        assert log.next_offset == 2

        # Prepare batch of 5 events that will force rotation to segment 1
        batch_events = [
            RawEvent(
                source="B", receive_timestamp=2.0, payload={"i": "B", "val": 10 + i}
            )
            for i in range(5)
        ]

        # Inject simulated disk write failure on fsync (after rotation)
        orig_fsync = os.fsync
        call_count = 0

        def fail_on_batch_commit(fd):
            nonlocal call_count
            call_count += 1
            if call_count >= 4:
                raise OSError("Simulated disk sync failure")
            return orig_fsync(fd)

        with mock.patch("os.fsync", side_effect=fail_on_batch_commit):
            with pytest.raises(OSError) as exc_info:
                log.append_batch(batch_events)
            assert "Simulated disk sync failure" in str(exc_info.value)

        # Offset must still be 2! No partial frames or orphan records
        assert log.next_offset == 2
        recs = list(log.iter_from(0))
        assert len(recs) == 2

        # Retry the batch without the injected failure: must succeed cleanly!
        retry_offsets = log.append_batch(batch_events)
        assert retry_offsets == [2, 3, 4, 5, 6]
        assert log.next_offset == 7

        all_recs = list(log.iter_from(0))
        assert len(all_recs) == 7
        offsets_seen = [off for off, _ in all_recs]
        # Strictly monotonic, zero duplicates!
        assert offsets_seen == [0, 1, 2, 3, 4, 5, 6]
    finally:
        log.close()


def test_f06_wal_verify_clean_and_corrupt(tmp_path):
    """F-06: IngestLog.verify() validates IEEE CRC-32 and detects corrupt frames."""
    log_dir = str(tmp_path / "wal_verify_test")
    log = IngestLog(log_dir)
    try:
        log.append_batch([RawEvent(source="SRC", payload={"k": i}) for i in range(10)])
    finally:
        log.close()

    report = IngestLog.verify(log_dir)
    assert report["is_clean"] is True
    assert report["valid_frames"] == 10
    assert report["corrupted_frames"] == 0
    assert report["corrupted_segments"] == 0
