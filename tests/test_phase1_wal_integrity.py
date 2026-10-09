"""Phase 1 Regression Suite: WAL Integrity, Frame Validation & Ack Semantics (Spec §5 & Task 2).

Verifies that:
1. AckStatus enum values represent documented durability boundaries.
2. Corrupted frame length (> 16 MB or max_segment_bytes) is rejected without unbounded memory allocation.
3. IngestLog.iter_from increments corrupted_frames_count on corrupt/oversized frames.
4. IngestLog enters poisoned state on unrecoverable I/O failures and blocks further appends.
"""

import os
import struct
import time
import zlib
import pytest

from mdrap.ingestlog import (
    IngestLog,
    AckStatus,
    FRAME_MAGIC,
    FRAME_HEADER_FORMAT,
    FRAME_HEADER_SIZE,
    MAX_FRAME_PAYLOAD_BYTES,
    IngestLogCorruptError,
    IngestLogError,
)
from mdrap.models import RawEvent


def test_ack_status_enum_values():
    """Verify AckStatus enum definitions against the durability contract."""
    assert AckStatus.RECEIVED == 1
    assert AckStatus.ACCEPTED == 2
    assert AckStatus.BUFFERED_APP == 3
    assert AckStatus.WRITTEN_OS == 4
    assert AckStatus.DURABLY_COMMITTED == 5
    assert AckStatus.RECOVERED == 6


def test_oversized_frame_length_rejected_without_oom(tmp_path):
    """Corrupted frame with 2 GB length does not cause OOM, triggers IngestLogCorruptError in body."""
    wal_dir = tmp_path / "oversized_wal"
    log = IngestLog(str(wal_dir))

    # Append one valid frame
    log.append(RawEvent(source="TEST", payload={"msg": "hello"}, receive_timestamp=time.time()))
    log.close()

    # Tamper segment by injecting an oversized frame header in the middle
    seg_files = [f for f in os.listdir(wal_dir) if f.startswith("segment_") and f.endswith(".log")]
    assert len(seg_files) == 1
    seg_path = os.path.join(wal_dir, seg_files[0])

    with open(seg_path, "a+b") as f:
        # Inject corrupted frame with 2 GB length (0x7FFFFFFF)
        corrupted_hdr = struct.pack(
            FRAME_HEADER_FORMAT,
            FRAME_MAGIC,
            0,
            1,  # offset
            time.time(),
            0x7FFFFFFF,  # 2 GB length!
            0x12345678,
        )
        f.write(corrupted_hdr)
        # Write additional trailing bytes so it appears in middle of log rather than torn tail EOF
        f.write(b"TRAIL" * 100)

    # Reopening should detect the oversized length and raise IngestLogCorruptError without OOM
    with pytest.raises(IngestLogCorruptError) as exc_info:
        IngestLog(str(wal_dir))

    assert "Oversized frame length" in str(exc_info.value)
    assert str(MAX_FRAME_PAYLOAD_BYTES) in str(exc_info.value)


def test_iter_from_tracks_corrupted_frames(tmp_path):
    """iter_from accurately tracks corrupted frames via corrupted_frames_count."""
    wal_dir = tmp_path / "corrupt_tracking_wal"
    log = IngestLog(str(wal_dir))

    # Append 3 valid records
    for i in range(3):
        log.append(RawEvent(source="TEST", payload={"i": i}, receive_timestamp=time.time()))
    log.close()

    seg_files = [f for f in os.listdir(wal_dir) if f.startswith("segment_") and f.endswith(".log")]
    seg_path = os.path.join(wal_dir, seg_files[0])

    # Corrupt the CRC of the middle record (offset 1)
    with open(seg_path, "r+b") as f:
        # Skip segment header (32 bytes) + 1st frame (header 28 + payload)
        # Find offset 1 frame header CRC location
        f.seek(32)
        h1 = f.read(FRAME_HEADER_SIZE)
        _, _, _, _, len1, _ = struct.unpack(FRAME_HEADER_FORMAT, h1)
        f.seek(32 + FRAME_HEADER_SIZE + len1)
        # At frame 2 header: CRC is the last 4 bytes of 28-byte header
        pos_crc = f.tell() + 24
        f.seek(pos_crc)
        f.write(b"\x00\x00\x00\x00")  # corrupt CRC

    # Iterate with iter_from
    records = list(log.iter_from(0))
    # Should recover records 0 and 2, while skipping corrupted record 1
    assert len(records) == 2
    assert records[0][0] == 0
    assert records[1][0] == 2
    # corrupted_frames_count must be incremented!
    assert log.corrupted_frames_count >= 1
    log.close()


def test_ingestlog_poisoning_on_io_failure(tmp_path, monkeypatch):
    """Simulated unrecoverable I/O failure poisons IngestLog, blocking subsequent appends."""
    wal_dir = tmp_path / "poison_wal"
    log = IngestLog(str(wal_dir))

    log.append(RawEvent(source="TEST", payload={"v": 1}, receive_timestamp=time.time()))

    # Force write to raise OSError and truncate to fail
    def mock_write(b):
        raise OSError("Disk full / I/O error")

    def mock_truncate(pos):
        raise OSError("Truncate failed")

    monkeypatch.setattr(log._current_file, "write", mock_write)
    monkeypatch.setattr(log._current_file, "truncate", mock_truncate)

    with pytest.raises(OSError):
        log.append(RawEvent(source="TEST", payload={"v": 2}, receive_timestamp=time.time()))

    assert log._is_poisoned is True

    # Subsequent appends must immediately fail with poisoned error
    with pytest.raises(IngestLogError) as exc:
        log.append(RawEvent(source="TEST", payload={"v": 3}, receive_timestamp=time.time()))
    assert "poisoned" in str(exc.value)

    log.close()
