"""
Test WAL-to-Snapshot-to-Replay Consistency and Grouped Durability Semantics (P0).

Verifies:
1. save_snapshot() offset calculation and SHA-256 integrity metadata.
2. Segment pruning preserving active segments and deleting strictly obsolete segments.
3. Full crash recovery: snapshot restoration + partial WAL replay yields 100% state parity.
4. Explicit grouped durability and ACK semantics across fsync policies.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import pytest

from mdrap.clock import SystemClock
from mdrap.engine import Engine, EngineConfig
from mdrap.models import RawEvent


@pytest.fixture
def temp_wal_dir():
    dirpath = tempfile.mkdtemp(prefix="mdrap_test_wal_consist_")
    yield dirpath
    shutil.rmtree(dirpath, ignore_errors=True)


def _make_event(i: int, symbol: str = "AAPL", price: float = 150.0) -> RawEvent:
    return RawEvent(
        source="FEED_A",
        payload={
            "instrument": symbol,
            "event_type": "TRADE",
            "price": price + (i * 0.1),
            "quantity": 10.0 + i,
            "exchange_ts": 1_000_000.0 + i,
            "sequence": i,
        },
        raw_id=f"raw_{i}",
    )


def test_save_snapshot_offset_and_integrity(temp_wal_dir):
    """Verify save_snapshot() accurately captures last_applied_offset and SHA-256."""
    cfg = EngineConfig(wal_dir=temp_wal_dir, fsync_policy="always")
    engine = Engine.open(temp_wal_dir, config=cfg)

    events = [_make_event(i) for i in range(25)]
    decisions = [engine.submit(ev) for ev in events]

    last_applied = engine.state.last_applied_offset
    assert last_applied == 24
    assert engine.state.event_count == 25

    res = engine.save_snapshot(prune_obsolete_segments=False)
    assert res["snapshot_offset"] == 24
    assert os.path.isfile(res["path"])

    with open(res["path"], "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["offset"] == 24
    assert data["last_applied_wal_offset"] == 24
    assert data["event_count"] == 25
    assert data["sha256"] == res["sha256"]
    assert "state" in data
    engine.close()


def test_segment_pruning_with_snapshot(temp_wal_dir):
    """Verify delete_segments_before correctly prunes obsolete segments while keeping active/needed."""
    # Tiny segments to force multiple segment rotations
    cfg = EngineConfig(
        wal_dir=temp_wal_dir, fsync_policy="always", max_segment_bytes=1024
    )
    engine = Engine.open(temp_wal_dir, config=cfg)

    # Submit 60 events across multiple segments
    for i in range(60):
        engine.submit(_make_event(i))

    seg_files_before = [f for f in os.listdir(temp_wal_dir) if f.endswith(".log")]
    assert len(seg_files_before) >= 3, (
        f"Expected multiple segments, got {seg_files_before}"
    )

    # Save snapshot at offset 25
    res = engine.save_snapshot(prune_obsolete_segments=True)
    assert res["snapshot_offset"] == 59
    assert res["segments_pruned"] >= 1

    seg_files_after = [f for f in os.listdir(temp_wal_dir) if f.endswith(".log")]
    assert len(seg_files_after) < len(seg_files_before)
    # Active segment must never be pruned
    assert len(seg_files_after) >= 1
    engine.close()


def test_wal_to_snapshot_to_replay_consistency(temp_wal_dir):
    """Verify that restarting from snapshot + replaying subsequent WAL records yields identical state."""
    cfg = EngineConfig(
        wal_dir=temp_wal_dir, fsync_policy="always", max_segment_bytes=2048
    )
    engine = Engine.open(temp_wal_dir, config=cfg)

    # 1. Submit first batch of 40 events
    for i in range(40):
        engine.submit(_make_event(i))

    # 2. Save snapshot at offset 39
    snap_info = engine.save_snapshot(prune_obsolete_segments=True)
    assert snap_info["snapshot_offset"] == 39

    # 3. Submit second batch of 30 events (offsets 40 through 69)
    for i in range(40, 70):
        engine.submit(_make_event(i))

    authoritative_state = engine.state.to_dict()
    assert authoritative_state["last_applied_offset"] == 69
    assert authoritative_state["event_count"] == 70
    engine.close()

    # 4. Open fresh engine on same wal_dir
    recovered_engine = Engine.open(temp_wal_dir, config=cfg)

    # Recovery metrics must reflect snapshot usage and exact replay count
    assert recovered_engine.recovery_metrics.snapshot_used is True
    assert recovered_engine.recovery_metrics.snapshot_offset == 39
    assert recovered_engine.recovery_metrics.replayed_records == 30

    recovered_state = recovered_engine.state.to_dict()
    # 5. Assert byte-for-byte / field-for-field parity between states
    assert recovered_state == authoritative_state
    assert recovered_state["last_applied_offset"] == 69
    assert recovered_state["event_count"] == 70
    assert recovered_state["bbo_state"] == authoritative_state["bbo_state"]
    assert recovered_state["counts"] == authoritative_state["counts"]
    assert recovered_state["dedup_window"] == authoritative_state["dedup_window"]
    recovered_engine.close()


def test_explicit_grouped_durability_and_ack(temp_wal_dir):
    """Verify durable status and explicit await_durable() / submit_microbatch() contracts."""
    # Use grouped_by_size to test non-immediate fsync
    cfg = EngineConfig(wal_dir=temp_wal_dir, fsync_policy="grouped_by_size")
    engine = Engine.open(temp_wal_dir, config=cfg)

    ev = _make_event(0)
    dec = engine.submit(ev)
    assert dec is not None

    # In grouped_by_size policy, a single small event is not immediately fsynced
    assert dec.durable is False
    assert engine.metrics()["log_lag"] == 1

    # Calling await_durable forces/waits for sync
    ok = engine.await_durable(dec, timeout=1.0)
    assert ok is True
    assert engine.synced_offset >= dec.offset
    assert engine.metrics()["log_lag"] == 0

    # submit_microbatch guarantees immediate grouped fsync
    batch_evs = [_make_event(i) for i in range(1, 6)]
    batch_decs = engine.submit_microbatch(batch_evs)
    assert len(batch_decs) == 5
    for d in batch_decs:
        assert d.durable is True
    assert engine.synced_offset == batch_decs[-1].offset
    assert engine.metrics()["log_lag"] == 0

    engine.close()
