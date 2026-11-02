"""Stage A: Comprehensive Snapshot Corruption, Retention, and Fail-Closed Recovery Tests.

Validates institutional requirements for crash-safe snapshot lifecycle:
1. Snapshot verification (SHA-256).
2. Multi-snapshot fallback (recover from older valid snapshot when latest is corrupt).
3. Fail-closed recovery with precise RecoveryError when WAL is incomplete.
4. Self-healing on corrupted snapshot manifest (fallback to directory scan).
5. Atomic publication (tmp files ignored, no partial writes).
6. Exact state hash and event count parity between live and recovered states.
"""

import glob
import hashlib
import json
import os
import shutil
import tempfile
import pytest

from mdrap import Engine, EngineConfig, RawEvent, QualityStatus
from mdrap.models import RecoveryError


def _make_event(
    i: int, inst: str = "BTC-USD", base_ts: float = 1_700_000_000.0
) -> RawEvent:
    return RawEvent(
        source="BINANCE",
        payload={
            "instrument": inst,
            "event_type": "QUOTE",
            "bid": 50000.0 + (i % 10) * 0.1,
            "ask": 50001.0 + (i % 10) * 0.1,
            "exchange_ts": base_ts + i * 0.01,
            "sequence": i,
        },
        receive_timestamp=base_ts + i * 0.01,
        raw_id=f"raw_{i}",
    )


def test_corrupt_latest_snapshot_falls_back_to_previous_valid_snapshot(tmp_path):
    """When latest snapshot is corrupt, recovery falls back to prior valid snapshot and replays WAL."""
    wal_dir = str(tmp_path / "wal")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="always", max_segment_bytes=4096)
    engine = Engine.open(wal_dir, config=cfg)

    # 1. First batch: 40 events -> Snapshot 1 at offset 39
    for i in range(40):
        engine.submit(_make_event(i))
    snap1 = engine.save_snapshot(prune_obsolete_segments=False)
    assert snap1["snapshot_offset"] == 39

    # 2. Second batch: 40 events -> Snapshot 2 at offset 79
    for i in range(40, 80):
        engine.submit(_make_event(i))
    snap2 = engine.save_snapshot(prune_obsolete_segments=False)
    assert snap2["snapshot_offset"] == 79

    # 3. Third batch: 20 events (offsets 80..99)
    for i in range(80, 100):
        engine.submit(_make_event(i))

    live_state = engine.state.to_dict()
    live_counts = dict(engine.state.counts)
    live_bytes = json.dumps(live_state, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    live_sha = hashlib.sha256(live_bytes).hexdigest()
    engine.close()

    # Deliberately corrupt latest snapshot (snap2) and the latest snapshot.json pointer
    with open(snap2["path"], "w", encoding="utf-8") as f:
        f.write('{"corrupted": true, "offset": 79}')
    with open(snap2["latest_path"], "w", encoding="utf-8") as f:
        f.write('{"corrupted": true, "offset": 79}')

    # Reopen: should fall back to snap1 (offset 39) and replay from 40 to 99
    recovered = Engine.open(wal_dir, config=cfg)
    assert recovered.recovery_metrics.snapshot_used is True
    assert recovered.recovery_metrics.snapshot_offset == 39
    assert recovered.state.event_count == 100
    assert recovered.state.last_applied_offset == 99
    assert dict(recovered.state.counts) == live_counts

    rec_bytes = json.dumps(
        recovered.state.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(rec_bytes).hexdigest() == live_sha
    recovered.close()


def test_corrupt_all_snapshots_with_pruned_wal_fails_closed(tmp_path):
    """When snapshots are corrupt and WAL history has been pruned, Engine.open() MUST fail closed."""
    wal_dir = str(tmp_path / "wal_fail_closed")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="never", max_segment_bytes=2048)
    engine = Engine.open(wal_dir, config=cfg)

    # Submit enough events across multiple segments
    for i in range(100):
        engine.submit(_make_event(i))

    # Snapshot and prune earlier segments
    snap = engine.save_snapshot(prune_obsolete_segments=True)
    assert snap["segments_pruned"] >= 1

    # More events
    for i in range(100, 120):
        engine.submit(_make_event(i))
    engine.close()

    # Corrupt all snapshot files in the directory
    for fpath in glob.glob(os.path.join(wal_dir, "snapshot*.json")):
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("corrupted_json_payload_without_sha")

    # Invariant: Reopening must NOT silently recover partial state; it must fail closed!
    with pytest.raises(RecoveryError) as exc_info:
        Engine.open(wal_dir, config=cfg)

    assert "Engine recovery failed" in str(exc_info.value)
    assert "Cannot reconstruct authoritative state without complete history" in str(
        exc_info.value
    )


def test_corrupted_manifest_falls_back_to_directory_scan(tmp_path):
    """Corrupted snapshot_manifest.json self-heals by discovering snapshot_*.json via directory scan."""
    wal_dir = str(tmp_path / "wal_manifest")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="always")
    engine = Engine.open(wal_dir, config=cfg)

    for i in range(30):
        engine.submit(_make_event(i))
    engine.save_snapshot()
    engine.close()

    # Corrupt snapshot_manifest.json with invalid syntax
    manifest_path = os.path.join(wal_dir, "snapshot_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("{malformed json [")

    # Opening should safely discover snapshot and restore without exception
    recovered = Engine.open(wal_dir, config=cfg)
    assert recovered.recovery_metrics.snapshot_used is True
    assert recovered.recovery_metrics.snapshot_offset == 29
    assert recovered.state.event_count == 30
    recovered.close()


def test_interrupted_snapshot_tmp_files_ignored(tmp_path):
    """Dangling .tmp files from killed/interrupted snapshot writers are ignored."""
    wal_dir = str(tmp_path / "wal_tmp")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="always")
    engine = Engine.open(wal_dir, config=cfg)

    for i in range(20):
        engine.submit(_make_event(i))
    engine.save_snapshot()
    engine.close()

    # Create dangling .tmp file
    tmp_file = os.path.join(wal_dir, "snapshot_0000000000000099.tmp.1234_5678")
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write("incomplete write content")

    recovered = Engine.open(wal_dir, config=cfg)
    assert recovered.recovery_metrics.snapshot_used is True
    assert recovered.recovery_metrics.snapshot_offset == 19
    assert recovered.state.event_count == 20
    recovered.close()


def test_snapshot_rotation_and_retention_limit(tmp_path):
    """Snapshot rotation maintains up to max_snapshots_retained files and cleans up older ones."""
    wal_dir = str(tmp_path / "wal_rot")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="never", max_snapshots_retained=3)
    engine = Engine.open(wal_dir, config=cfg)

    # Create 5 snapshots sequentially
    for batch in range(5):
        for i in range(batch * 10, (batch + 1) * 10):
            engine.submit(_make_event(i))
        engine.save_snapshot(prune_obsolete_segments=False)

    engine.close()

    versioned_snaps = sorted(
        [
            f
            for f in os.listdir(wal_dir)
            if f.startswith("snapshot_")
            and f.endswith(".json")
            and f != "snapshot_manifest.json"
        ]
    )
    # Must retain exactly max_snapshots_retained = 3 snapshots
    assert len(versioned_snaps) == 3
    # Latest snapshot must be for offset 49
    assert "snapshot_0000000000000049.json" in versioned_snaps
