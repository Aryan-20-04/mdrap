"""Final Validation: Multi-Snapshot Fallback, Pruning Resilience, and Fail-Closed Recovery.

Fulfills P0 Priority 4 requirements:
1. 100 events -> Snapshot A
2. 100 events -> Snapshot B
3. 100 events -> Snapshot C
4. Prune WAL
5. Corrupt snapshot C -> Replay recovers from snapshot B
6. Corrupt snapshot B -> Replay recovers from snapshot A
7. Corrupt snapshot A -> System fails closed with RecoveryError
8. Concurrent ingestion during snapshot creation
9. Simulated failure during snapshot creation (atomic rename, no partial publish)
10. Checksum tampering detection
"""

import glob
import hashlib
import json
import os
import threading
import time
import pytest
from unittest.mock import patch

from mdrap import Engine, EngineConfig, RawEvent, QualityStatus
from mdrap.models import RecoveryError


def _make_event(
    seq: int,
    source: str = "BINANCE",
    inst: str = "BTC-USD",
    base_ts: float = 1_700_000_000.0,
) -> RawEvent:
    return RawEvent(
        source=source,
        payload={
            "instrument": inst,
            "event_type": "QUOTE",
            "bid": 50000.0 + (seq % 10) * 0.1,
            "ask": 50001.0 + (seq % 10) * 0.1,
            "exchange_ts": base_ts + seq * 0.01,
            "sequence": seq,
        },
        receive_timestamp=base_ts + seq * 0.01,
        raw_id=f"raw_{source}_{seq}",
    )


def test_cascading_snapshot_corruption_and_fail_closed(tmp_path):
    """Exact sequence: Snap A -> Snap B -> Snap C -> Prune -> Corrupt C -> Recover B -> Corrupt B -> Recover A -> Corrupt A -> Fail closed."""
    wal_dir = str(tmp_path / "wal_cascading")
    cfg = EngineConfig(
        wal_dir=wal_dir,
        fsync_policy="always",
        max_segment_bytes=1024,  # Small segments to force multiple segment files
        max_snapshots_retained=3,
        min_wal_snapshots_retained=2,
    )

    # Step 1: Ingest 100 events -> Snapshot A
    eng = Engine.open(wal_dir, config=cfg)
    for i in range(100):
        eng.submit(_make_event(i))
    snap_a = eng.save_snapshot(prune_obsolete_segments=False)
    assert snap_a["snapshot_offset"] == 99

    # Step 2: Ingest 100 events -> Snapshot B
    for i in range(100, 200):
        eng.submit(_make_event(i))
    snap_b = eng.save_snapshot(prune_obsolete_segments=False)
    assert snap_b["snapshot_offset"] == 199

    # Step 3: Ingest 100 events -> Snapshot C
    for i in range(200, 300):
        eng.submit(_make_event(i))
    snap_c = eng.save_snapshot(prune_obsolete_segments=False)
    assert snap_c["snapshot_offset"] == 299

    # Step 4: Prune WAL with retention for 2 fallback snapshots (A and B retained)
    prune_res = eng.save_snapshot(prune_obsolete_segments=True)
    assert prune_res["segments_pruned"] >= 1  # Older segments before A should be pruned

    target_live_state = eng.state.to_dict()
    target_live_sha = hashlib.sha256(
        json.dumps(target_live_state, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assert eng.state.event_count == 300
    eng.close()

    # Verify snapshot metadata requirements
    with open(snap_c["path"], "r", encoding="utf-8") as f:
        meta_c = json.load(f)
    assert meta_c["offset"] == 299
    assert meta_c["sha256"] is not None
    assert meta_c["timestamp"] > 0
    assert "earliest_wal_offset_covered" in meta_c
    assert "latest_wal_offset_included" in meta_c

    # Step 5: Corrupt snapshot C.
    # Replay recovers from snapshot B (offset 199) + replays WAL from 200 to 299.
    with open(snap_c["path"], "w", encoding="utf-8") as f:
        f.write('{"corrupted": true, "offset": 299}')
    with open(snap_c["latest_path"], "w", encoding="utf-8") as f:
        f.write('{"corrupted": true, "offset": 299}')

    eng_rec_b = Engine.open(wal_dir, config=cfg)
    assert eng_rec_b.recovery_metrics.snapshot_used is True
    assert eng_rec_b.recovery_metrics.snapshot_offset == 199
    assert eng_rec_b.state.event_count == 300
    assert eng_rec_b.state.last_applied_offset == 299
    rec_b_sha = hashlib.sha256(
        json.dumps(
            eng_rec_b.state.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert rec_b_sha == target_live_sha
    eng_rec_b.close()

    # Step 6: Corrupt snapshot B as well.
    # Replay recovers from snapshot A (offset 99) + replays WAL from 100 to 299.
    with open(snap_b["path"], "w", encoding="utf-8") as f:
        f.write('{"corrupted": true, "offset": 199}')

    eng_rec_a = Engine.open(wal_dir, config=cfg)
    assert eng_rec_a.recovery_metrics.snapshot_used is True
    assert eng_rec_a.recovery_metrics.snapshot_offset == 99
    assert eng_rec_a.state.event_count == 300
    assert eng_rec_a.state.last_applied_offset == 299
    rec_a_sha = hashlib.sha256(
        json.dumps(
            eng_rec_a.state.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert rec_a_sha == target_live_sha
    eng_rec_a.close()

    # Step 7: Corrupt snapshot A as well.
    # Now ALL snapshots are corrupt. Since WAL history before offset 100 was pruned,
    # the system MUST fail closed with RecoveryError rather than silently producing partial state!
    with open(snap_a["path"], "w", encoding="utf-8") as f:
        f.write('{"corrupted": true, "offset": 99}')

    with pytest.raises(RecoveryError) as exc_info:
        Engine.open(wal_dir, config=cfg)

    assert "Engine recovery failed" in str(exc_info.value)
    assert "Cannot reconstruct authoritative state without complete history" in str(
        exc_info.value
    )


def test_concurrent_ingestion_during_snapshot_creation(tmp_path):
    """Snapshot creation does not block concurrent event ingestion and captures consistent point-in-time."""
    wal_dir = str(tmp_path / "wal_concurrent")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="never")
    eng = Engine.open(wal_dir, config=cfg)

    ingest_count = 500
    ingest_errors = []

    def _ingest_worker():
        try:
            for i in range(ingest_count):
                eng.submit(_make_event(i))
                time.sleep(0.0002)
        except Exception as e:
            ingest_errors.append(e)

    t_worker = threading.Thread(target=_ingest_worker)
    t_worker.start()

    # Trigger snapshots while ingestion is ongoing
    time.sleep(0.02)
    snap1 = eng.save_snapshot()
    time.sleep(0.02)
    snap2 = eng.save_snapshot()

    t_worker.join()
    assert not ingest_errors

    assert snap1["snapshot_offset"] <= snap2["snapshot_offset"]
    assert eng.state.event_count == ingest_count
    eng.close()

    # Reopening uses the newest valid snapshot and replays remaining events
    recovered = Engine.open(wal_dir, config=cfg)
    assert recovered.state.event_count == ingest_count
    recovered.close()


def test_snapshot_atomic_rollback_on_simulated_io_error(tmp_path):
    """Simulated disk error during snapshot write leaves existing valid snapshots intact."""
    wal_dir = str(tmp_path / "wal_simulated_err")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="never")
    eng = Engine.open(wal_dir, config=cfg)

    for i in range(50):
        eng.submit(_make_event(i))
    _valid_snap = eng.save_snapshot()

    for i in range(50, 80):
        eng.submit(_make_event(i))

    # Simulate disk full / OSError during atomic replace
    with patch("os.replace", side_effect=OSError("No space left on device")):
        with pytest.raises(OSError):
            eng.save_snapshot()

    eng.close()

    # Reopen should cleanly recover using the prior valid snapshot
    rec = Engine.open(wal_dir, config=cfg)
    assert rec.recovery_metrics.snapshot_used is True
    assert rec.recovery_metrics.snapshot_offset == 49
    assert rec.state.event_count == 80
    rec.close()


def test_snapshot_checksum_tampering_detection(tmp_path):
    """Deliberate modification of snapshot content causes SHA-256 verification failure."""
    wal_dir = str(tmp_path / "wal_tamper")
    cfg = EngineConfig(wal_dir=wal_dir, fsync_policy="never")
    eng = Engine.open(wal_dir, config=cfg)

    for i in range(50):
        eng.submit(_make_event(i))
    snap = eng.save_snapshot()
    eng.close()

    # Tamper with event count in snapshot JSON without updating sha256
    with open(snap["path"], "r", encoding="utf-8") as f:
        data = json.load(f)
    data["state"]["event_count"] = 999999  # malicious/tampered state
    with open(snap["path"], "w", encoding="utf-8") as f:
        json.dump(data, f)

    # Reopening should reject the tampered snapshot because SHA mismatch is detected
    rec = Engine.open(wal_dir, config=cfg)
    # Because WAL was not pruned and snapshot was rejected, it reconstructed cleanly from WAL 0..49
    assert rec.recovery_metrics.snapshot_used is False
    assert rec.state.event_count == 50
    rec.close()
