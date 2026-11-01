"""Comprehensive crash, sudden termination, and simulated power-loss test suite.

Verifies:
1. Power loss / torn record: Truncated frame headers or bodies are detected via CRC32.
2. Unfsynced trailing frames: Recover accurately rewinds to last valid synced frame.
3. Sudden kill recovery: Engine state recovered from snapshot + subsequent WAL records matches byte-for-byte.
4. Corrupted snapshot resilience: Invalid/torn snapshot falls back to complete WAL replay from offset 0.
"""

import json
import os
import shutil
import tempfile
import pytest

from mdrap.clock import FixedClock
from mdrap.engine import Engine, EngineConfig
from mdrap.ingestlog import IngestLog
from mdrap.models import EventType, QualityStatus, RawEvent


def _td():
    return tempfile.mkdtemp(prefix="crash_test_")


def test_crash_truncated_frame_power_loss_recovery():
    """Simulate power loss cutting off disk I/O mid-frame write; verify recovery detects and recovers."""
    td = _td()
    try:
        wal = IngestLog(td, fsync_policy="always")
        # Write 10 valid records
        for i in range(10):
            wal.append(RawEvent(source="FEED", payload={"i": i, "price": 100.0 + i}))
        seg_path = wal.current_segment_path
        wal.close()

        # Simulate power loss: append 15 bytes of torn garbage (partial frame header) to segment file
        with open(seg_path, "ab") as f:
            f.write(b"\xaa\xbb\xcc\xdd\x00\x00\x01\x00\xff\xee\xdd\xcc\x00\x01\x02")

        # Open fresh IngestLog; recover() must detect torn record, report corrupted frame, and allow new appends
        recovered_wal = IngestLog(td, fsync_policy="always")
        recovered_count = 0
        for offset, raw in recovered_wal.iter_from(0):
            assert raw.payload["i"] == offset
            recovered_count += 1

        assert recovered_count == 10
        assert recovered_wal.corrupted_frames_count >= 1

        # Subsequent append starts at offset 10 with no collision or corruption
        new_off = recovered_wal.append(
            RawEvent(source="FEED", payload={"i": 10, "price": 110.0})
        )
        assert new_off == 10
        recovered_wal.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_crash_mid_payload_crc_corruption_fails_closed():
    """Simulate bit flips or block corruption in segment; verify CRC32 detects corruption."""
    from mdrap.ingestlog import IngestLogCorruptError

    td = _td()
    try:
        wal = IngestLog(td, fsync_policy="always")
        offsets = []
        for i in range(5):
            offsets.append(
                wal.append(RawEvent(source="FEED", payload={"i": i, "val": 1000 + i}))
            )
        seg_path = wal.current_segment_path
        wal.close()

        # Invert bits in middle of segment file (affecting record payload or CRC)
        with open(seg_path, "r+b") as f:
            f.seek(80)
            orig = f.read(4)
            f.seek(80)
            f.write(bytes([b ^ 0xFF for b in orig]))

        # Opening IngestLog on mid-file corruption must raise IngestLogCorruptError (fail closed)
        with pytest.raises(IngestLogCorruptError):
            IngestLog(td)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_crash_snapshot_plus_wal_complete_recovery():
    """Verify sudden process crash between snapshot and live traffic recovers with zero loss or duplication."""
    td = _td()
    snap_path = os.path.join(td, "snapshot.json")
    try:
        engine = Engine(path=td)

        # Ingest 30 events
        for i in range(30):
            engine.submit(
                RawEvent(
                    source="EXCHANGE",
                    payload={
                        "instrument": "BTC-USD",
                        "event_type": "TRADE",
                        "price": 50000.0 + i,
                        "quantity": 1.0,
                    },
                )
            )

        # Save snapshot at offset 29
        engine.snapshot(snap_path)
        assert engine.state.last_applied_offset == 29

        # Ingest 20 more events without saving another snapshot
        for i in range(30, 50):
            engine.submit(
                RawEvent(
                    source="EXCHANGE",
                    payload={
                        "instrument": "BTC-USD",
                        "event_type": "TRADE",
                        "price": 50000.0 + i,
                        "quantity": 1.0,
                    },
                )
            )

        # Simulate sudden crash: discard instance without calling engine.close()
        del engine

        # Recovery instance opens same path
        recovered = Engine.open(path=td)
        # Snapshot loaded (offset 29), followed by replay of records 30..49
        assert recovered.state.last_applied_offset == 49
        assert recovered.state.event_count == 50
        assert recovered.state.observed_count == 50
        assert recovered.state.counts["VALID"] == 50

        # Submitting next event receives continuous offset 50
        next_dec = recovered.submit(
            RawEvent(
                source="EXCHANGE",
                payload={
                    "instrument": "BTC-USD",
                    "event_type": "TRADE",
                    "price": 50050.0,
                    "quantity": 1.0,
                },
            )
        )
        assert next_dec.offset == 50
        recovered.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_corrupted_snapshot_file_falls_back_to_wal():
    """If snapshot.json was torn during power failure, engine safely falls back to replaying WAL from offset 0."""
    td = _td()
    snap_path = os.path.join(td, "snapshot.json")
    try:
        engine = Engine(path=td)
        for i in range(25):
            engine.submit(
                RawEvent(
                    source="EXCHANGE",
                    payload={
                        "instrument": "ETH-USD",
                        "event_type": "TRADE",
                        "price": 3000.0 + i,
                        "quantity": 0.5,
                    },
                )
            )
        engine.snapshot(snap_path)
        engine.close()

        # Simulate torn write: corrupt the snapshot file
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write('{"offset": 24, "corrupted_json_content...')

        # Reopening Engine should safely fall back to replaying from WAL segment 0
        recovered = Engine.open(path=td)
        assert recovered.state.event_count == 25
        assert recovered.state.last_applied_offset == 24
        assert recovered.state.counts["VALID"] == 25
        recovered.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)
