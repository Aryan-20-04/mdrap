"""Universal tests for WAL rotation, batch atomicity, and rotation failures (W-03, W-04, W-05)."""

from __future__ import annotations

import os
from unittest import mock
import pytest

from mdrap.ingestlog import IngestLog, IngestLogPoisonedError
from mdrap.models import RawEvent
from tests.helpers.faults import fail_commit_fsync


def _make_event(i: int, source: str = "FEED") -> RawEvent:
    return RawEvent(
        source=source,
        payload={"instrument": "AAPL", "price": 150.0 + i, "sequence": i},
        receive_timestamp=1700000000.0 + i,
        raw_id=f"evt_{i}",
    )


def test_w03_batch_commit_failure_same_process_retry(tmp_path):
    """W-03: Batch commit failure leaves disk unchanged; retry produces contiguous offsets."""
    log_dir = str(tmp_path / "wal_w03")
    log = IngestLog(log_dir, max_segment_bytes=100_000)
    try:
        # Pre-populate with 3 events
        assert log.append(_make_event(0)) == 0
        assert log.append(_make_event(1)) == 1
        assert log.append(_make_event(2)) == 2

        batch = [_make_event(i) for i in range(3, 8)]

        # Inject commit failure
        with fail_commit_fsync():
            with pytest.raises(OSError) as exc_info:
                log.append_batch(batch)
            assert "batch commit boundary" in str(exc_info.value)

        # Offsets and disk remain strictly at 3
        assert log.next_offset == 3
        disk_events = list(log.iter_from(0))
        assert len(disk_events) == 3
        assert [off for off, _ in disk_events] == [0, 1, 2]

        # Same-process retry succeeds
        new_offsets = log.append_batch(batch)
        assert new_offsets == [3, 4, 5, 6, 7]
        assert log.next_offset == 8

        all_events = list(log.iter_from(0))
        assert len(all_events) == 8
        assert [off for off, _ in all_events] == [0, 1, 2, 3, 4, 5, 6, 7]
    finally:
        log.close()


def test_w04_batch_never_spans_segments(tmp_path):
    """W-04: Batch larger than remaining segment space lands entirely in one new segment."""
    log_dir = str(tmp_path / "wal_w04")
    # Small segment size: ~2KB per segment
    log = IngestLog(log_dir, max_segment_bytes=2_048)
    try:
        # Append events to fill most of segment 0
        for i in range(5):
            log.append(_make_event(i))

        seg0_file = log._current_filename
        seg0_size_before = os.path.getsize(seg0_file)

        # Append batch of 15 events which cannot fit into remaining space
        batch = [_make_event(i) for i in range(5, 20)]
        offsets = log.append_batch(batch)

        assert offsets == list(range(5, 20))
        # The batch must have triggered rotation before writing, so seg0 was closed
        seg0_size_after = os.path.getsize(seg0_file)
        assert seg0_size_after == seg0_size_before

        # All events in the batch must reside in the new segment
        seg1_file = log._current_filename
        assert seg1_file != seg0_file

        all_records = list(log.iter_from(0))
        assert len(all_records) == 20
        assert [off for off, _ in all_records] == list(range(20))
    finally:
        log.close()


def test_w05_rotation_failure_poisons_and_recovers(tmp_path):
    """W-05: Rotation failure poisons log; reopen recovers with contiguous offsets."""
    log_dir = str(tmp_path / "wal_w05")
    log = IngestLog(log_dir, max_segment_bytes=1_024)
    try:
        # Write 3 events in segment 0
        for i in range(3):
            log.append(_make_event(i))

        assert log.next_offset == 3

        # Inject failure during next rotation
        def failing_rotate(start_offset):
            log._is_poisoned = True
            raise OSError("Injected disk failure during segment rotation")

        with mock.patch.object(
            log, "_rotate_to_new_segment", side_effect=failing_rotate
        ):
            # Attempting to write a large batch or event that triggers rotation
            with pytest.raises(OSError) as exc_info:
                log.append_batch([_make_event(i) for i in range(3, 10)])
            assert "rotation" in str(exc_info.value).lower()

        # Log is poisoned
        assert log._is_poisoned is True
        with pytest.raises(IngestLogPoisonedError):
            log.append(_make_event(99))

        with pytest.raises(IngestLogPoisonedError):
            log.append_batch([_make_event(100)])
    finally:
        log.close()

    # Reopen to recover: must open cleanly, verify valid frames, and accept new events
    log_recovered = IngestLog(log_dir, max_segment_bytes=100_000)
    try:
        assert log_recovered.next_offset == 3
        recs = list(log_recovered.iter_from(0))
        assert len(recs) == 3
        assert [off for off, _ in recs] == [0, 1, 2]

        # New appends succeed monotonically
        assert log_recovered.append(_make_event(3)) == 3
        assert log_recovered.append_batch([_make_event(4), _make_event(5)]) == [4, 5]
        assert log_recovered.next_offset == 6
        assert [off for off, _ in log_recovered.iter_from(0)] == [0, 1, 2, 3, 4, 5]
    finally:
        log_recovered.close()
