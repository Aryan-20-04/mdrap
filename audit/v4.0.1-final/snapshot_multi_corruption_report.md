# Multi-Snapshot Fallback, Integrity Verification, and Crash Safety

## 1. Executive Summary

A critical vulnerability identified in early MDRAP builds was single-point snapshot vulnerability:
If older WAL segments were pruned upon creating a snapshot, and that snapshot subsequently suffered bit-rot, torn write, or checksum corruption, the system was left with incomplete history. In experiment R2, deliberate corruption of a single snapshot caused the engine to drop from 3,100 committed events to 239 events (~2,861 events permanently lost).

In MDRAP v4.0.1, **snapshot lifecycle and recovery have been re-engineered for fail-safe institutional durability**:
1. **Multi-Candidate Fallback**: If the latest snapshot is corrupt, recovery falls back automatically to preceding snapshots ($C \rightarrow B \rightarrow A$) and replays the WAL forward.
2. **Fail-Closed Guarantee**: If all snapshots are corrupt and WAL history has been pruned, MDRAP strictly refuses to start and raises `RecoveryError("Cannot reconstruct authoritative state without complete history")`, preventing silent state truncation.
3. **Atomic Publication**: Snapshots are written to unique PID/timestamp-scoped temporary files, fsynced to disk, validated via SHA-256 readback, and published atomically via `os.replace()` and directory fsync.

---

## 2. Recovery Decision Tree

```mermaid
flowchart TD
    A["Engine.open(wal_dir)"] --> B["Discover Candidate Snapshots (sorted desc by offset)"]
    B --> C{"Any Unchecked Candidate?"}
    C -- "No / List Empty" --> D{"Is WAL Complete from offset 0?"}
    D -- "Yes" --> E["Replay Entire WAL from Offset 0"]
    D -- "No (WAL Pruned)" --> F["Raise RecoveryError: Fail Closed!"]
    
    C -- "Yes" --> G["Read Candidate Snapshot"]
    G --> H{"Verify SHA-256 Checksum"}
    H -- "Mismatch / Corrupt" --> I["Log Warning<br/>Mark Candidate Failed"] --> C
    H -- "Valid" --> J{"Is WAL Continuous from Snapshot Offset + 1?"}
    J -- "No (Missing WAL Segments)" --> K["Log Warning: WAL History Gap"] --> C
    J -- "Yes (Usable Snapshot)" --> L["Restore EngineState from Snapshot<br/>Replay WAL Forward from Offset + 1"]
    L --> M["Recovery Complete: Authoritative State Restored"]
```

---

## 3. Atomic Publication Protocol

```
1. Capture state snapshot dictionary & monotonic offset under engine lock (< 500ns)
2. Serialize to canonical sorted JSON bytes and compute SHA-256 hash (outside lock)
3. Open temporary file: snapshot_0000000000000299.json.tmp.<pid>_<timestamp>
4. Write JSON payload, flush(), and execute os.fsync(fileno)
5. Read back temporary file and verify calculated SHA-256 matches expected SHA-256
6. Execute atomic replace: os.replace(tmp_path, versioned_path)
7. Execute directory entry fsync: _fsync_dir(snap_dir)
8. Atomically update snapshot_manifest.json with candidate manifest
```

---

## 4. Verification Evidence

Validated in `tests/final/test_multiple_snapshot_corruption.py`:
- `test_cascading_snapshot_corruption_and_fail_closed`:
  - Snapshot A (offset 99) + B (offset 199) + C (offset 299) created across 300 events.
  - WAL pruned with `min_wal_snapshots_retained=2`.
  - Corrupt snapshot C: Replay recovers from snapshot B (offset 199) + replays WAL 200..299. Exact state SHA matches live state.
  - Corrupt snapshot B: Replay recovers from snapshot A (offset 99) + replays WAL 100..299. Exact state SHA matches live state.
  - Corrupt snapshot A: All snapshots corrupt with incomplete WAL history $\rightarrow$ Engine raises `RecoveryError` and fails closed.
- `test_concurrent_ingestion_during_snapshot_creation`: Ingestion continues at full rate during snapshot serialization without thread deadlocks.
- `test_snapshot_atomic_rollback_on_simulated_io_error`: Simulated `OSError("No space left on device")` aborts snapshot write cleanly, leaving prior valid snapshots intact.
- `test_snapshot_checksum_tampering_detection`: Bit-flipped snapshot payloads are detected via SHA-256 and rejected.
