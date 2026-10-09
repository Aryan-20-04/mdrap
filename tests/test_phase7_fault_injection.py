"""MDRAP Phase 7 — Automated Fault Injection & Failure Recovery Test Suite.

Automates the 5 scenarios specified in audit/phase7/fault_injection_matrix.md:
1. FI-01: Partial Write / Trailing Byte Truncation
2. FI-02: Corrupted Payload CRC32 Checksum Detection
3. FI-03: Slow Consumer Socket Backpressure & Eviction
4. FI-04: Partition Ownership Fencing Collision Fail-Closed
5. FI-05: Corrupted Oversized Payload Framing Bound Protection
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
import time
import zlib
import pytest

from mdrap.models import RawEvent
from mdrap.ingestlog import IngestLog, FRAME_HEADER_FORMAT, FRAME_HEADER_SIZE, FRAME_MAGIC
from mdrap.partition import ConsumerFanoutManager, ShardConfig, ShardInstance, SymbolPartitioner
from mdrap.historical_verifier import HistoricalVerifier


def test_fault_injection_trailing_truncation():
    """FI-01: Verify that an abrupt crash leaving trailing truncated bytes recovers cleanly."""
    tmp_dir = tempfile.mkdtemp()
    try:
        log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
        for i in range(1, 21):
            raw = RawEvent(
                raw_id=f"raw-{i}",
                receive_timestamp=float(time.time_ns()),
                source="FAULT_FEED",
                payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": 1.0, "sequence": i},
            )
            log.append(raw)
        log.close()

        # Find segment file and artificially append 17 corrupted bytes (partial frame header)
        seg_files = [f for f in os.listdir(tmp_dir) if f.endswith(".log") or f.endswith(".seg")]
        assert len(seg_files) > 0
        seg_path = os.path.join(tmp_dir, seg_files[0])

        with open(seg_path, "ab") as f:
            f.write(b"\xAA\x55\x00\x00\x12\x34\x56")  # Truncated partial frame

        # Recover using reader
        reader_log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
        recovered_events = [raw for _, raw in reader_log.iter_from(0)]
        reader_log.close()

        # Exactly 20 valid events must be recovered; truncated tail must not crash reader
        assert len(recovered_events) == 20
        assert recovered_events[-1].payload["sequence"] == 20
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fault_injection_checksum_mutation():
    """FI-02: Verify that a flipped byte in a WAL frame payload is caught by the verifier."""
    tmp_dir = tempfile.mkdtemp()
    try:
        log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
        for i in range(1, 11):
            raw = RawEvent(
                raw_id=f"raw-{i}",
                receive_timestamp=float(time.time_ns()),
                source="FAULT_FEED",
                payload={"instrument": "MSFT", "event_type": "TRADE", "price": 200.0, "quantity": 2.0, "sequence": i},
            )
            log.append(raw)
        log.close()

        seg_files = [f for f in os.listdir(tmp_dir) if f.endswith(".log") or f.endswith(".seg")]
        seg_path = os.path.join(tmp_dir, seg_files[0])

        # Mutate 1 byte in the middle of the segment
        with open(seg_path, "r+b") as f:
            f.seek(64)  # Byte offset inside record 1
            original_byte = f.read(1)
            f.seek(64)
            f.write(b"\xFF" if original_byte != b"\xFF" else b"\x00")

        # Run historical verifier
        verifier = HistoricalVerifier()
        report = verifier.verify_ingestlog_segment(seg_path)

        # Must flag corruption
        assert report.status == "FAIL"
        assert report.crc32_invalid_count >= 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fault_injection_slow_consumer_eviction():
    """FI-03: Verify slow consumer is evicted upon >= 10 drops while fast consumers continue."""
    fanout = ConsumerFanoutManager(max_buffer_per_client=5)
    slow_client = fanout.register_consumer("slow-1", "tenant-1")
    fast_client = fanout.register_consumer("fast-1", "tenant-2")

    # Fast client actively drains queue; slow client does not drain
    # Send 25 events (slow client queue holds 5, then drops 20 events)
    for i in range(1, 26):
        fanout.broadcast_event("AAPL", b"TICK_FRAME")
        # Drain fast client queue immediately
        if not fast_client.stream_queue.empty():
            fast_client.stream_queue.get_nowait()

    # Slow client should have exceeded 10 drops and been evicted
    assert slow_client.frames_dropped >= 10
    assert not slow_client.is_active

    # Fast client should have received events with zero drops
    assert fast_client.is_active
    assert fast_client.frames_dropped == 0


def test_fault_injection_fencing_collision():
    """FI-04: Verify second instance cannot bind directory when lock is held."""
    tmp_dir = tempfile.mkdtemp()
    try:
        # Launch primary instance with exclusive lock
        log1 = IngestLog(log_dir=tmp_dir, lock=True)

        # Attempt to launch secondary instance on same directory
        from mdrap.ingestlog import IngestLogLockedError
        with pytest.raises(IngestLogLockedError):
            _ = IngestLog(log_dir=tmp_dir, lock=True)

        log1.close()

        # After primary closes, new instance can acquire cleanly
        log2 = IngestLog(log_dir=tmp_dir, lock=True)
        log2.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fault_injection_oversized_payload_protection():
    """FI-05: Verify parser bounds ceiling prevents OOM on corrupted length prefix."""
    tmp_dir = tempfile.mkdtemp()
    try:
        log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
        raw = RawEvent(
            raw_id="raw-1",
            receive_timestamp=float(time.time_ns()),
            source="FEED",
            payload={"instrument": "GOOG", "event_type": "TRADE", "price": 175.0, "quantity": 10.0, "sequence": 1},
        )
        log.append(raw)
        log.close()

        seg_files = [f for f in os.listdir(tmp_dir) if f.endswith(".log") or f.endswith(".seg")]
        seg_path = os.path.join(tmp_dir, seg_files[0])

        # Forge frame with corrupted length of 100 MB (exceeds 16 MB ceiling)
        with open(seg_path, "ab") as f:
            forged_hdr = struct.pack(
                FRAME_HEADER_FORMAT,
                FRAME_MAGIC,
                0,
                2,  # offset 2
                time.time(),
                100 * 1024 * 1024,  # 100 MB length
                0x12345678,
            )
            f.write(forged_hdr)

        from mdrap.ingestlog import IngestLogCorruptError
        with pytest.raises(IngestLogCorruptError):
            _ = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
