"""Tests for IngestLog: framing, CRC32, torn-tail recovery, rotation, and fsync."""

import os
import struct
import zlib
import pytest

from mdrap.ingestlog import (
    IngestLog,
    IngestLogCorruptError,
    FRAME_MAGIC,
    FRAME_HEADER_FORMAT,
    FRAME_HEADER_SIZE,
    SEGMENT_HEADER_SIZE,
)
from mdrap.models import RawEvent


def test_ingestlog_append_and_iteration(tmp_path):
    log_dir = str(tmp_path / "ingest_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    events = [
        RawEvent(
            source="BINANCE",
            payload={"instrument": "BTC-USDT", "price": 50000.0 + i, "exchange_ts": 1700000000.0 + i},
            receive_timestamp=1700000000.0 + i,
            raw_id=f"raw_{i}",
        )
        for i in range(10)
    ]

    offsets = [log.append(ev) for ev in events]
    assert offsets == list(range(10))
    assert log.next_offset == 10
    log.close()

    # Reopen and iterate
    log2 = IngestLog(log_dir=log_dir)
    recovered = list(log2.iter_from(0))
    assert len(recovered) == 10
    for off, rev in recovered:
        assert rev.raw_id == f"raw_{off}"
        assert rev.source == "BINANCE"
        assert rev.payload["price"] == 50000.0 + off
    log2.close()


def test_ingestlog_torn_tail_truncated_cleanly(tmp_path):
    log_dir = str(tmp_path / "torn_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    for i in range(5):
        log.append(RawEvent(source="TEST", payload={"instrument": "AAPL", "price": 100.0 + i}))
    log.close()

    # Append half a frame header to simulate a crash during header write
    segments = log._list_segment_files()
    assert len(segments) == 1
    seg_path = segments[0][1]

    original_size = os.path.getsize(seg_path)
    with open(seg_path, "ab") as f:
        # Incomplete frame header (only 10 bytes instead of 28)
        f.write(b"INCOMPLETE")

    assert os.path.getsize(seg_path) == original_size + 10

    # Reopen log - should detect torn tail, truncate back to original_size, and recover all 5 events
    recovered_log = IngestLog(log_dir=log_dir)
    assert os.path.getsize(seg_path) == original_size
    recs = list(recovered_log.iter_from(0))
    assert len(recs) == 5
    assert recovered_log.next_offset == 5

    # New writes should cleanly succeed with monotonic offsets
    new_off = recovered_log.append(RawEvent(source="TEST", payload={"instrument": "AAPL", "price": 105.0}))
    assert new_off == 5
    recovered_log.close()


def test_ingestlog_incomplete_payload_truncated(tmp_path):
    log_dir = str(tmp_path / "torn_payload_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    for i in range(3):
        log.append(RawEvent(source="TEST", payload={"instrument": "MSFT", "price": 200.0 + i}))
    log.close()

    segments = log._list_segment_files()
    seg_path = segments[0][1]
    valid_size = os.path.getsize(seg_path)

    # Inject frame header with payload_len = 100, but only write 20 bytes of payload
    with open(seg_path, "ab") as f:
        hdr = struct.pack(
            FRAME_HEADER_FORMAT,
            FRAME_MAGIC,
            0,
            3,
            1700000000.0,
            100,  # declared 100 bytes
            123456,
        )
        f.write(hdr)
        f.write(b"A" * 20)  # only 20 bytes written before simulated crash!

    # Reopen - should repair incomplete frame
    repaired_log = IngestLog(log_dir=log_dir)
    assert os.path.getsize(seg_path) == valid_size
    recs = list(repaired_log.iter_from(0))
    assert len(recs) == 3
    repaired_log.close()


def test_ingestlog_bitflip_detected(tmp_path):
    log_dir = str(tmp_path / "bitflip_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    for i in range(5):
        log.append(RawEvent(source="TEST", payload={"instrument": "NVDA", "price": 300.0 + i}))
    log.close()

    segments = log._list_segment_files()
    seg_path = segments[0][1]

    # Flip bits in the middle of record 2
    with open(seg_path, "r+b") as f:
        f.seek(SEGMENT_HEADER_SIZE + FRAME_HEADER_SIZE + 10)  # Inside payload of record 0
        b = f.read(1)
        f.seek(-1, os.SEEK_CUR)
        f.write(bytes([b[0] ^ 0xFF]))  # Invert byte

    # Recovery must detect middle-of-log bit corruption!
    with pytest.raises(IngestLogCorruptError):
        IngestLog(log_dir=log_dir)


def test_ingestlog_segment_rotation_and_checkpoint_deletion(tmp_path):
    log_dir = str(tmp_path / "rotated_log")
    # Tiny segments to force rotation after every few records
    log = IngestLog(log_dir=log_dir, max_segment_bytes=200, fsync_policy="always")

    for i in range(15):
        log.append(RawEvent(source="TEST", payload={"instrument": "SPY", "price": 400.0 + i}))
    log.close()

    segments = log._list_segment_files()
    assert len(segments) > 1, f"Expected multiple segments, got {len(segments)}"

    # Delete segments before offset 10
    deleted = log.delete_segments_before(checkpoint_offset=10)
    assert deleted > 0

    remaining_segments = log._list_segment_files()
    assert len(remaining_segments) < len(segments)
