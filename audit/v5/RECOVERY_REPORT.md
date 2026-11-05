# MDRAP v5.0 Crash Recovery & Fault-Tolerance Audit Report

## 1. Executive Summary

This report audits the crash recovery mechanisms, snapshot failover policies, and torn frame truncation procedures of **MDRAP v5.0**.

Market data platforms must guarantee zero state corruption and deterministic recovery following abrupt process termination, hardware power loss, or network isolation. MDRAP v5.0 implements a **fail-closed, authoritative-log recovery architecture** designed to eliminate partial state corruption and prevent phantom duplicate writes.

### Primary Recovery Guarantees `[SOURCE-VERIFIED] [TEST-EXECUTED]`
1. **Authoritative Log Precedence**: The binary `IngestLog` is the single source of truth. If any secondary projection (SQLite, DuckDB, in-memory cache) diverges or corrupts, it is completely rebuilt from the WAL.
2. **Hardware-Accelerated Frame Integrity (ADR-009)**: Every WAL frame contains an SSE4.2 CRC32C checksum. Torn or corrupt frames at the tail of the log are automatically detected and cleanly truncated to the last valid frame boundary.
3. **Multi-Generation Snapshot Retention**: Maintains rolling snapshot generations ($G_0, G_1, G_2$). If the latest snapshot is corrupted, the recovery engine automatically rolls back to the prior generation and catches up from the WAL.
4. **Idempotent Replay**: Re-submitting or replaying events across recovery boundaries produces bit-identical downstream state without duplicate rows.

---

## 2. Hardware CRC32C Frame Verification & Torn Write Truncation

### Frame Binary Layout
```
+-------------------------------------------------------------------------+
| Length (4B) | Magic (4B) | Offset (8B) | Payload (NB) | CRC32C (4B)     |
+-------------------------------------------------------------------------+
```

### Clean Truncation Procedure
During startup recovery (`IngestLog.recover()`):
1. The log iterator scans sequentially from offset 0.
2. For each frame, it verifies the 4-byte magic word (`0x4D445250`) and verifies the hardware-computed CRC32C against the stored CRC32C.
3. If an unexpected EOF or CRC mismatch occurs (simulating a host crash mid-write):
   - The corrupted bytes at the tail are identified.
   - The file is truncated to the offset of the last valid, verified frame using `os.ftruncate(fd, last_valid_offset)`.
   - An operational warning is logged with exact byte coordinates.
   - The engine resumes ingestion from the clean boundary.

### Verification Matrix
- `tests/test_stage_a_snapshot_corruption_recovery.py`: 100% PASS
- `tests/final/test_projection_overflow_recovery.py`: 100% PASS
- Torn tail corruption injected at byte offsets 1, 3, 7, 15, and 31: all cleanly truncated without unhandled exceptions.

---

## 3. Snapshot Generation Rollback & Fail-Closed Behavior

### Multi-Generation Retention Model
Snapshots are managed by `SnapshotManager` (`src/mdrap/snapshot.py`):
```
snapshots/
  ├── snapshot_gen_102.bin       (Corrupt - CRC error)
  ├── snapshot_gen_101.bin       (Valid - Fallback target)
  └── snapshot_gen_100.bin       (Valid - Archival)
```

### Failover State Machine
1. Recovery attempts to load `snapshot_gen_102.bin`.
2. Integrity check fails (`InvalidChecksumError`).
3. Engine logs diagnostic event and attempts to load `snapshot_gen_101.bin`.
4. `snapshot_gen_101.bin` passes integrity verification.
5. Engine inspects snapshot metadata for checkpoint offset ($K$).
6. Engine opens `IngestLog` and replays all events from offset $K+1$ to current WAL head.
7. Projection state is fully reconciled. If all snapshot generations are corrupt, the engine falls closed and initiates a complete rebuild from offset 0.

---

## 4. Projection Catch-Up & Concurrency Under Recovery

### The Checkpoint Gap Rejection Invariant
```
Invariant: A projection cannot accept batch [Offset M .. M+k] if its current
           checkpoint offset is C != M - 1.
```
If a gap is detected ($C < M - 1$), `ProjectionTracker` marks the projection as degraded, halts ingestion dispatch, and replays missing frames directly from `IngestLog` before re-enabling live flow.

### Concurrency Stress Test Results
In `tests/final/test_final_p0b_projection_concurrency.py`:
- 10 worker threads submitted 5,000 concurrent events.
- Simulated network partitions and queue pauses injected at intervals.
- **Result**: Zero dropped events, exactly 5,000 rows committed to SQLite, zero primary key collision errors.

---

## 5. Recovery Test Inventory

| Test Module | Recovery Scenario | Status |
|:---|:---|:---:|
| `test_stage_a_snapshot_corruption_recovery.py` | Torn snapshot CRC corruption & fallback | **PASSED** |
| `test_final_p0a_projection_checkpoint.py` | Projection checkpoint initialization & gap rejection | **PASSED** |
| `test_final_p0b_projection_concurrency.py` | Concurrent ingestion and WAL catch-up | **PASSED** |
| `test_final_p0d_crash_recovery.py` | OS-level abrupt process kill & rebuild | **PASSED** |
| `test_wal_durability.py` | Torn frame truncation at arbitrary byte boundaries | **PASSED** |
