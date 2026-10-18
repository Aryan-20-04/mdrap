"""Tests for WAL Format Version 2 (W-08, W-09, F-06).

Validates:
1. Full header + payload CRC coverage in v2 (every field in the 28-byte frame header
   is protected: magic, ver, offset, ts, length, crc).
2. Backward compatibility: legacy format version 1 WAL segments can be verified,
   opened, iterated, and appended to without data loss.
3. Negative proof: bit-flipping any frame header field in v2 causes immediate CRC/magic
   detection, whereas v1 only protected the payload.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
import zlib
import pytest

from mdrap.ingestlog import (
    DEFAULT_MAX_SEGMENT_BYTES,
    FRAME_HEADER_FORMAT,
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    LOG_MAGIC,
    LOG_VERSION,
    LOG_VERSION_V1,
    LOG_VERSION_V2,
    SEGMENT_HEADER_FORMAT,
    SEGMENT_HEADER_SIZE,
    SUPPORTED_LOG_VERSIONS,
    IngestLog,
    IngestLogCorruptError,
    compute_frame_crc,
    pack_frame_header,
)
from mdrap.models import RawEvent


def _create_sample_raw(idx: int) -> RawEvent:
    return RawEvent(
        source="CME",
        payload={
            "instrument": "ES",
            "price": 5000.0 + idx,
            "quantity": 1.0 + idx,
            "sequence": 1000 + idx,
        },
        receive_timestamp=1700000000.0 + idx,
        raw_id=f"raw_es_{idx}",
    )


def test_w08_header_corruption_every_field_detected(tmp_path):
    """W-08 / F-06: Every field in the 28-byte frame header must be protected by CRC or magic check.

    Fields tested:
    - bytes 0..1:   magic (0xAA55) -> detected by magic check
    - bytes 2..3:   version (uint16) -> detected by CRC32
    - bytes 4..11:  offset (uint64) -> detected by CRC32
    - bytes 12..19: timestamp (float64) -> detected by CRC32
    - bytes 20..23: length (uint32) -> detected by CRC32 / length bounds
    - bytes 24..27: crc32 (uint32) -> detected by CRC32
    """
    field_byte_offsets = {
        "magic": 0,
        "version": 2,
        "offset": 4,
        "timestamp": 12,
        "length": 20,
        "crc32": 24,
    }

    for field_name, byte_offset in field_byte_offsets.items():
        case_dir = str(tmp_path / f"corrupt_{field_name}")
        log = IngestLog(log_dir=case_dir, fsync_policy="always")
        log.append(_create_sample_raw(0))
        log.append(_create_sample_raw(1))
        log.close()

        # Locate segment
        seg_files = log._list_segment_files()
        assert len(seg_files) == 1
        seg_path = seg_files[0][1]

        # Target the FIRST frame header (starts right after SEGMENT_HEADER_SIZE)
        target_pos = SEGMENT_HEADER_SIZE + byte_offset

        with open(seg_path, "r+b") as f:
            f.seek(target_pos)
            orig_byte = f.read(1)
            assert len(orig_byte) == 1
            # Flip high bit
            corrupted_byte = bytes([orig_byte[0] ^ 0x80])
            f.seek(target_pos)
            f.write(corrupted_byte)

        # 1. Verify static scanner catches the corruption
        report = IngestLog.verify(case_dir)
        assert report["is_clean"] is False, (
            f"Verify failed to detect corruption in '{field_name}'"
        )
        assert report["corrupted_frames"] >= 1 or report["corrupted_segments"] >= 1

        # 2. Verify opening the log refuses or reports corruption on corrupted non-tail frame
        # IngestLog should detect that frame 0 is corrupt
        with pytest.raises(IngestLogCorruptError):
            IngestLog(log_dir=case_dir)


def test_w09_format_versioning_v1_compatibility(tmp_path):
    """W-09: v1 segments (CRC over payload only) must be verified, opened, and appended to cleanly."""
    log_dir = str(tmp_path / "v1_compat_wal")
    os.makedirs(log_dir, exist_ok=True)
    seg_path = os.path.join(log_dir, "segment_00000000000000000000.log")

    # Manually write a valid v1 segment file
    start_offset = 0
    now = 1700000000.0
    seg_hdr_prefix = struct.pack(">8sHQd", LOG_MAGIC, LOG_VERSION_V1, start_offset, now)
    seg_crc = zlib.crc32(seg_hdr_prefix) & 0xFFFFFFFF
    seg_hdr = seg_hdr_prefix + struct.pack(">I2s", seg_crc, b"\x00\x00")
    assert len(seg_hdr) == SEGMENT_HEADER_SIZE

    v1_events = [_create_sample_raw(i) for i in range(5)]

    with open(seg_path, "wb") as f:
        f.write(seg_hdr)
        for i, ev in enumerate(v1_events):
            payload = json.dumps(
                {
                    "raw_id": ev.raw_id,
                    "source": ev.source,
                    "payload": ev.payload,
                    "receive_timestamp": ev.receive_timestamp,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            # In v1: CRC is ONLY over payload
            crc = zlib.crc32(payload) & 0xFFFFFFFF
            frame_hdr = struct.pack(
                FRAME_HEADER_FORMAT,
                FRAME_MAGIC,
                LOG_VERSION_V1,
                i,
                now + i,
                len(payload),
                crc,
            )
            f.write(frame_hdr)
            f.write(payload)

    # 1. IngestLog.verify on v1 segment must pass cleanly
    report = IngestLog.verify(log_dir)
    assert report["is_clean"] is True, f"v1 segment verify failed: {report['errors']}"
    assert report["valid_frames"] == 5
    assert report["corrupted_frames"] == 0

    # 2. Open IngestLog on v1 segment: must read all records
    log = IngestLog(log_dir=log_dir)
    assert log.next_offset == 5
    recs = list(log.iter_from(0))
    assert len(recs) == 5
    for i, (off, r_ev) in enumerate(recs):
        assert off == i
        assert r_ev.raw_id == f"raw_es_{i}"
        assert r_ev.payload["price"] == 5000.0 + i

    # 3. Append new records (which will be written in format v2)
    new_ev = _create_sample_raw(5)
    new_off = log.append(new_ev)
    assert new_off == 5
    assert log.next_offset == 6
    log.close()

    # 4. Verify hybrid log with both v1 and v2 records
    report_hybrid = IngestLog.verify(log_dir)
    assert report_hybrid["is_clean"] is True
    assert report_hybrid["valid_frames"] == 6

    # 5. Read back entire sequence from 0 to 5
    log2 = IngestLog(log_dir=log_dir)
    all_recs = list(log2.iter_from(0))
    assert len(all_recs) == 6
    assert [off for off, _ in all_recs] == list(range(6))
    log2.close()


def test_v1_vs_v2_header_crc_negative_proof(tmp_path):
    """Direct proof of F-06 / W-08 fix:

    In v1, mutating the offset field in the header prefix is NOT detected by CRC
    (because v1 CRC covers payload only).
    In v2, mutating the offset field is GUARANTEED to be detected by CRC!
    """
    payload = b'{"test":"data"}'
    v1_crc = compute_frame_crc(b"", payload, version=LOG_VERSION_V1)
    assert v1_crc == (zlib.crc32(payload) & 0xFFFFFFFF)

    hdr_prefix_a = struct.pack(
        ">HHQdI", FRAME_MAGIC, LOG_VERSION_V2, 100, 1.0, len(payload)
    )
    hdr_prefix_b = struct.pack(
        ">HHQdI", FRAME_MAGIC, LOG_VERSION_V2, 200, 1.0, len(payload)
    )

    v2_crc_a = compute_frame_crc(hdr_prefix_a, payload, version=LOG_VERSION_V2)
    v2_crc_b = compute_frame_crc(hdr_prefix_b, payload, version=LOG_VERSION_V2)

    # Different header prefix with same payload MUST yield different CRC in v2
    assert v2_crc_a != v2_crc_b
    assert v2_crc_a != v1_crc
