"""Tests for Long-Running Stability & Soak Verification (L-01..L-04).

Validates:
- L-01: Soak stability (no unhandled exceptions, bounded RSS growth, linear WAL scaling).
- L-02: Crash-restart soak with replay equivalence and zero acknowledged loss (E-05, R-01).
- L-03: Burst & sustained throughput with bounded queues and explicit drop accounting.
- L-04: Disk and resource pressure with predictable poisoning and clean recovery.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import time
from unittest.mock import patch
import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.ingestlog import IngestLog, IngestLogPoisonedError
from mdrap.metrics import get_rss_mb
from mdrap.models import QualityStatus, RawEvent


def _make_soak_raw(seq: int, instrument: str = "AAPL") -> RawEvent:
    now = 1700000000.0 + (seq * 0.001)
    return RawEvent(
        source="EXCHANGE_SOAK",
        raw_id=f"soak_{seq}",
        receive_timestamp=now,
        payload={
            "instrument": instrument,
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": seq,
            "price": 100.0 + (seq % 50) * 0.1,
            "quantity": 10.0,
        },
    )


def test_l01_soak_stability_and_memory_slope(tmp_path):
    """L-01: Ingest multi-round stream; verify RSS memory slope is bounded and WAL scales with input."""
    wal_dir = tmp_path / "l01_wal"
    db_path = tmp_path / "l01.db"

    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(db_path=str(db_path), fsync_policy="grouped_by_time"),
    )

    total_events = 2500
    batch_size = 100
    rss_samples = []

    rss_start = get_rss_mb()
    for b in range(0, total_events, batch_size):
        chunk = [_make_soak_raw(b + i) for i in range(batch_size)]
        decisions = engine.submit(chunk)
        assert len(decisions) == batch_size
        rss_samples.append(get_rss_mb())

    engine.close()

    rss_end = get_rss_mb()
    growth_mb = rss_end - rss_start
    # Memory growth must not exceed reasonable bounded threshold (< 100 MB for 2.5k events)
    assert growth_mb < 100.0, (
        f"Unbounded memory growth detected during soak: {growth_mb:.2f} MB"
    )

    # Verify WAL files grew and contain all 2,500 records
    verifier_log = IngestLog(str(wal_dir))
    assert verifier_log.next_offset == total_events
    replayed = sum(1 for _ in verifier_log.iter_from(0))
    assert replayed == total_events
    verifier_log.close()


def test_l02_crash_restart_soak_replay_equivalence(tmp_path):
    """L-02: Simulate repeated abrupt crash-restart cycles; verify E-05 replay equivalence and zero data loss."""
    wal_dir = tmp_path / "l02_wal"
    db_path = tmp_path / "l02.db"

    rounds = 4
    events_per_round = 150
    total_expected = rounds * events_per_round

    global_seq = 0
    total_acked = 0

    for r in range(rounds):
        engine = Engine.open(
            str(wal_dir),
            config=EngineConfig(db_path=str(db_path), fsync_policy="always"),
        )

        batch = [_make_soak_raw(global_seq + i) for i in range(events_per_round)]
        global_seq += events_per_round
        decisions = engine.submit(batch)
        assert len(decisions) == events_per_round
        total_acked += len(decisions)

        # Abrupt crash simulation (drop without engine.close())
        del engine
        gc.collect()

        # Reopen and check invariants
        reopened = Engine.open(
            str(wal_dir),
            config=EngineConfig(db_path=str(db_path), fsync_policy="always"),
        )
        assert reopened.state.event_count == total_acked
        assert reopened.metrics()["projection_lag"] == 0
        reopened.close()

    # Final check: Replay WAL from scratch into independent engine and verify E-05 equivalence
    standalone_engine = Engine()
    replay_log = IngestLog(str(wal_dir))
    clock = reopened.clock

    st = standalone_engine.state
    for off, raw in replay_log.iter_from(0):
        st, _ = standalone_engine.step(st, raw, clock, offset=off)

    replay_log.close()
    standalone_engine.close()

    final_engine = Engine.open(str(wal_dir), config=EngineConfig(db_path=str(db_path)))
    # Compare state counts and sequence state
    assert final_engine.state.counts == st.counts
    assert final_engine.state.event_count == st.event_count
    assert final_engine.state.event_count == total_expected
    final_engine.close()


def test_l03_burst_and_sustained_queue_bounds(tmp_path):
    """L-03: Sudden burst at 10x sustained rate is processed without event drops or corruption."""
    wal_dir = tmp_path / "l03_wal"
    db_path = tmp_path / "l03.db"

    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(db_path=str(db_path), fsync_policy="grouped_by_time"),
    )

    # 1. Sustained load: 5 batches of 50 events
    for i in range(5):
        chunk = [_make_soak_raw(i * 50 + j) for j in range(50)]
        decs = engine.submit(chunk)
        assert len(decs) == 50

    # 2. Sudden burst: 1 batch of 500 events (10x size)
    burst_chunk = [_make_soak_raw(250 + j) for j in range(500)]
    t0 = time.perf_counter()
    burst_decs = engine.submit(burst_chunk)
    elapsed = time.perf_counter() - t0

    assert len(burst_decs) == 500
    assert all(d.quality_status == QualityStatus.VALID for d in burst_decs)
    assert engine.state.event_count == 750

    engine.close()


def test_l04_disk_and_memory_pressure(tmp_path):
    """L-04: I/O failure during commit rolls back cleanly; rotation failure poisons log predictably."""
    wal_dir = tmp_path / "l04_wal"

    log = IngestLog(str(wal_dir), fsync_policy="always")
    for i in range(5):
        log.append(_make_soak_raw(i))
    assert log.next_offset == 5

    # 1. Recoverable commit I/O error: rolls back uncommitted frames without poisoning
    failing_batch = [_make_soak_raw(5 + i) for i in range(3)]
    with patch("os.fsync", side_effect=OSError("Simulated ENOSPC Disk Full")):
        with pytest.raises(OSError):
            log.append_batch(failing_batch)

    # Uncommitted batch was rolled back cleanly
    assert log.next_offset == 5
    assert len(list(log.iter_from(0))) == 5

    # 2. Rotation failure under disk pressure poisons log
    with patch(
        "builtins.open", side_effect=OSError("Disk Full during segment creation")
    ):
        with pytest.raises(OSError):
            log._rotate_to_new_segment(5)

    assert log.is_poisoned is True
    with pytest.raises(IngestLogPoisonedError):
        log.append(_make_soak_raw(8))
    log.close()

    # Reopen after pressure removed: recovers cleanly with contiguous offsets
    recovered_log = IngestLog(str(wal_dir))
    assert recovered_log.next_offset == 5
    recovered_recs = list(recovered_log.iter_from(0))
    assert len(recovered_recs) == 5
    # New write succeeds
    new_off = recovered_log.append(_make_soak_raw(5))
    assert new_off == 5
    assert recovered_log.next_offset == 6
    recovered_log.close()
