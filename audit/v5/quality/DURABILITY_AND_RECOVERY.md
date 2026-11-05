# MDRAP v5.0 Durability, Persistence & Crash Recovery Audit Findings

## 1. Scope & Verification Strategy

This audit inspects the Write-Ahead Log (WAL) append mechanics, physical filesystem synchronization contracts, projection checkpoint tracking, torn write truncation, and process crash recovery in MDRAP v5.0.

> [!CAUTION]
> **Audit Principle on Durability Verification**: Process termination (`SIGKILL` / `TerminateProcess`) tests verify operating system handle cleanup and user-space recovery state machines. They **do not prove power-loss safety alone**. True power-loss durability requires verifying that physical `FlushFileBuffers` / `fdatasync` system calls are invoked before acknowledgement tokens are released.

---

## 2. WAL Durability & Fsync Findings

### Finding DUR-01: Durability Policy Enforcement
- **Classification**: `[SOURCE-VERIFIED]` `[BENCHMARK-MEASURED]`
- **Source Location**: `src/mdrap/ingestlog.py:1001`
- **Analysis**:
  - `fsync_policy == "always"`: Calls `os.fsync(self._current_file.fileno())` synchronously on every batch append. Throughput is bounded by physical disk NVMe flush latency: **304.8 EPS (3,148 $\mu$s p50)**.
  - `fsync_policy == "grouped_by_size"`: Fsyncs when `_bytes_since_fsync >= 64 KB`.
  - `fsync_policy == "grouped_by_time"`: Fsyncs when `time.time() - _last_fsync_ts >= 0.05` (50 ms).
  - `fsync_policy == "never"`: Fsync is delegated to the OS kernel background writeback.
- **Finding**: Policies are cleanly decoupled and faithfully enforced.
- **Verdict**: **COMPLIANT**.

### Finding DUR-02: Unconditional `flush()` in `append_batch`
- **Classification**: `[SOURCE-VERIFIED]` `[BENCHMARK-MEASURED]`
- **Source Location**: `src/mdrap/ingestlog.py:999`
- **Code**:
  ```python
  write_pos = self._current_file.tell()
  self._current_file.write(batch_buffer)
  self._current_file.flush()
  ```
- **Finding**: `self._current_file.flush()` (flushing the C stdio/Python buffer to the OS file cache via `WriteFile`) is called on **every single event/batch**, even when `fsync_policy="never"`! In the cProfile trace, this accounts for **8.5% of total pipeline runtime (0.089s)**.
- **Optimization Recommendation**: When `fsync_policy == "never"`, rely on the standard 64 KB / 128 KB Python buffered I/O stream flush rather than invoking an explicit flush per tick.
- **Severity**: **Performance Bottleneck (Low Risk)**.

### Finding DUR-03: Acknowledgement Semantics & Offset Precedence
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/ingestlog.py:1019`, `src/mdrap/engine.py:1364`
- **Specification**: In institutional trading pipelines, the client receives an acknowledgement token `offset = K` only after frame $K$ has met the durability policy constraint.
- **Finding**: In `IngestLog`, `_synced_offset` is only updated when `os.fsync()` succeeds, and `_group_commit_cv.notify_all()` unblocks waiting callers.
- **Verdict**: **COMPLIANT**.

---

## 3. Projection Durability & Checkpoint Recovery

### Finding DUR-04: Contiguous Checkpoint Progression
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/projection.py:165`
- **Specification**: A projection checkpoint cannot advance over a gap of unapplied offsets. If offset 100 fails, the checkpoint must remain at 99 even if offset 101 succeeds.
- **Finding**: In `ProjectionTracker`:
  - Contiguous batches are applied in order.
  - If an exception occurs, `self.failed_offsets.append(last_off)` is recorded, and `last_contiguous_offset` halts advancement.
  - The projection enters `DEGRADED` status until missing offsets are replayed from the authoritative WAL.
- **Audit Execution**: Tested in `tests/final/test_final_p0a_projection_checkpoint.py` (100% pass rate).
- **Verdict**: **COMPLIANT**.

### Finding DUR-05: Multi-Generation Snapshot Retention & Fallback
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/snapshot.py:SnapshotManager`
- **Specification**: If the latest snapshot is corrupted (e.g. power failure during snapshot creation), the engine must automatically fall back to the preceding generation ($G-1$) and replay forward from the WAL.
- **Audit Execution**: Tested via `tests/test_stage_a_snapshot_corruption_recovery.py`. Injected CRC corruption into generation 102; engine safely fell back to generation 101 and caught up 1,000 missing events without state divergence.
- **Verdict**: **COMPLIANT**.

### Finding DUR-06: Torn Frame Truncation via CRC32C
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/ingestlog.py:iter_from`, `src/mdrap/ingestlog.py:_verify_candidate_frame`
- **Finding**: During recovery scan, every frame header is verified with CRC32C. If an incomplete or torn frame is encountered at the end of the log file, the engine truncates the file back to the last valid frame offset, preventing partial record poisoning.
- **Verdict**: **COMPLIANT**.
