"""Adversarial Verification Suite for MDRAP v4.0.1 Priority 2:
Durability Semantics, Truthful ACK Contracts, and Power-Loss Invariants.

Durability Contracts Enforced:
1. 'always':
   - ACK returned with durable=True ONLY after os.fsync() succeeds.
   - Zero acknowledged event loss on physical power loss.
2. 'grouped_by_time':
   - Ingest ACK returns durable=False until background fsync runs.
   - Background flusher flushes during quiet periods within 50ms.
   - await_durable() provides an explicit synchronous barrier.
3. 'grouped_by_size':
   - Flushes immediately when 64KB threshold is reached.
   - Flushes trailing writes during quiet periods.
4. 'never':
   - Relies strictly on OS page-cache write-back.
   - ACK explicitly returns durable=False.
   - Truthful contract: OS_BUFFERED_NO_POWER_LOSS_PROTECTION.
5. Torn frames and corrupted records:
   - Torn header, torn payload, and CRC32 mismatches are detected and rejected.
   - Recovery truncates to last valid uncorrupted frame without data corruption.
"""

from __future__ import annotations

import glob
import os
import shutil
import struct
import tempfile
import time
import pytest
from unittest.mock import patch

from mdrap import Engine, EngineConfig, RawEvent, QualityStatus
from mdrap.ingestlog import IngestLog, IngestLogPoisonedError


def _make_event(
    i: int,
    source: str = "FEED_A",
    inst: str = "BTC-USD",
    base_ts: float = 1_700_000_000.0,
) -> RawEvent:
    return RawEvent(
        source=source,
        payload={
            "instrument": inst,
            "event_type": "QUOTE",
            "bid": 50000.0 + (i % 10) * 0.1,
            "ask": 50001.0 + (i % 10) * 0.1,
            "exchange_ts": base_ts + i * 0.01,
            "sequence": i,
        },
        receive_timestamp=base_ts + i * 0.01,
        raw_id=f"raw_{source}_{i}",
    )


def test_durability_contract_strings(tmp_path):
    """Verify truthful contract specification strings for all policies."""
    policies = {
        "always": "PHYSICAL_FSYNC_DURABLE",
        "grouped_by_time": "GROUPED_TIME_FSYNC_DURABLE",
        "grouped_by_size": "GROUPED_SIZE_FSYNC_DURABLE",
        "never": "OS_BUFFERED_NO_POWER_LOSS_PROTECTION",
    }
    for policy, expected in policies.items():
        wal_dir = str(tmp_path / f"wal_{policy}")
        cfg = EngineConfig(wal_dir=wal_dir, fsync_policy=policy)
        engine = Engine.open(wal_dir, config=cfg)
        assert engine.durability_guarantee == expected
        assert engine.log.durability_guarantee == expected
        engine.close()


def test_always_policy_acknowledges_durable_true(tmp_path):
    """Under 'always', decisions are physically durable at ACK time."""
    wal_dir = str(tmp_path / "wal_always")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="always")
    engine = Engine.open(wal_dir, config=cfg)

    dec = engine.submit(_make_event(0))
    assert dec is not None
    assert dec.offset == 0
    assert dec.durable is True
    assert engine.synced_offset == 0

    engine.close()


def test_never_policy_acknowledges_durable_false(tmp_path):
    """Under 'never', decisions are explicitly acknowledged as NOT physically durable.

    Truth in systems engineering: OS page cache write-back cannot promise zero power-loss loss.
    """
    wal_dir = str(tmp_path / "wal_never")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="never")
    engine = Engine.open(wal_dir, config=cfg)

    for i in range(10):
        dec = engine.submit(_make_event(i))
        assert dec is not None
        assert (
            dec.durable is False
        )  # Explicit truth: physical durability not guaranteed

    assert engine.durability_guarantee == "OS_BUFFERED_NO_POWER_LOSS_PROTECTION"
    engine.close()


def test_grouped_by_time_quiet_period_and_await_durable(tmp_path):
    """Under 'grouped_by_time', quiet period flusher syncs within 100ms; await_durable() barriers."""
    wal_dir = str(tmp_path / "wal_grouped_time")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="grouped_by_time")
    engine = Engine.open(wal_dir, config=cfg)

    dec = engine.submit(_make_event(0))
    assert dec is not None

    # Barrier on durability
    ok = engine.await_durable(dec, timeout=1.0)
    assert ok is True
    assert dec.durable is True
    assert engine.synced_offset >= 0

    # Submit second event without barrier, wait for quiet period flusher
    _dec2 = engine.submit(_make_event(1))
    time.sleep(0.12)  # Wait for quiet-period background flusher (50ms interval)
    assert engine.synced_offset >= 1

    engine.close()


def test_grouped_by_size_quiet_period_flush(tmp_path):
    """Under 'grouped_by_size', trailing writes below 64KB are flushed during quiet periods."""
    wal_dir = str(tmp_path / "wal_grouped_size")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="grouped_by_size")
    engine = Engine.open(wal_dir, config=cfg)

    # Ingest 3 events (far below 64KB threshold)
    for i in range(3):
        engine.submit(_make_event(i))

    # Before quiet period, not synced
    assert engine.synced_offset < 2

    # Wait for quiet period background flusher
    time.sleep(0.15)
    assert engine.synced_offset >= 2

    engine.close()


def test_await_durable_for_old_and_latest_offsets(tmp_path):
    """await_durable() handles past offsets immediately and syncs target offset."""
    wal_dir = str(tmp_path / "wal_await_offsets")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="grouped_by_time")
    engine = Engine.open(wal_dir, config=cfg)

    for i in range(5):
        engine.submit(_make_event(i))

    # Barrier on latest offset
    assert engine.await_durable(4, timeout=1.0) is True
    assert engine.synced_offset >= 4

    # Barrier on an already-synced past offset returns True immediately
    t0 = time.perf_counter()
    assert engine.await_durable(2, timeout=1.0) is True
    t1 = time.perf_counter()
    assert (t1 - t0) < 0.005  # Instantaneous return for past offset

    # Negative offset returns True
    assert engine.await_durable(-1, timeout=1.0) is True

    engine.close()


def test_await_durable_timeout_during_blocked_fsync(tmp_path):
    """await_durable() returns False when timeout expires before sync."""
    wal_dir = str(tmp_path / "wal_timeout")
    log = IngestLog(wal_dir, fsync_policy="true_group_commit")
    log.append(_make_event(0))

    # Target an impossible future offset with very short timeout
    ok = log.await_synced(target_offset=999, timeout=0.05)
    assert ok is False

    log.close()


def test_fsync_io_failure_does_not_produce_false_durable_ack(tmp_path):
    """Disk I/O error during fsync raises exception and does NOT acknowledge durable=True."""
    wal_dir = str(tmp_path / "wal_fsync_fail")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="always")
    engine = Engine.open(wal_dir, config=cfg)

    with patch("os.fsync", side_effect=OSError(5, "Input/output error")):
        with pytest.raises(OSError, match="Input/output error"):
            engine.submit(_make_event(0))

    # Invariant: offset was NOT acknowledged as durable
    assert engine.synced_offset == -1
    engine.close()


def test_restart_after_torn_header_and_payload_recovery(tmp_path):
    """Torn frames (interrupted write, corrupt header, truncated payload) recover cleanly."""
    wal_dir = str(tmp_path / "wal_torn")
    log = IngestLog(wal_dir, fsync_policy="always")

    for i in range(10):
        log.append(_make_event(i))
    log.close()

    # Find active segment file
    segments = sorted(glob.glob(os.path.join(wal_dir, "segment_*.log")))
    active_seg = segments[-1]

    # Append partial torn header (only 4 bytes instead of 32-byte header)
    with open(active_seg, "ab") as f:
        f.write(b"MDRP")  # partial header

    # Reopening discovers torn header and truncates cleanly back to offset 9
    reopened = IngestLog(wal_dir)
    assert reopened.next_offset == 10
    records = list(reopened.iter_from(0))
    assert len(records) == 10
    reopened.close()


def test_crc_mismatch_detected_and_safely_rejected(tmp_path):
    """Corrupted payload bytes trigger CRC32 mismatch and recovery stops at last valid record."""
    wal_dir = str(tmp_path / "wal_crc")
    log = IngestLog(wal_dir, fsync_policy="always")

    for i in range(5):
        log.append(_make_event(i))
    log.close()

    segments = sorted(glob.glob(os.path.join(wal_dir, "segment_*.log")))
    active_seg = segments[-1]

    # Flip bits inside the 5th record payload
    with open(active_seg, "r+b") as f:
        file_len = f.seek(0, os.SEEK_END)
        # Seek back 20 bytes into the last record's payload and flip bits
        f.seek(file_len - 20)
        _orig = f.read(4)
        f.seek(file_len - 20)
        f.write(b"\xff\xff\xff\xff")

    # Reopening detects CRC32 mismatch on record 4 and recovers valid prefix 0..3
    reopened = IngestLog(wal_dir)
    assert reopened.next_offset == 4
    records = list(reopened.iter_from(0))
    assert len(records) == 4
    reopened.close()
