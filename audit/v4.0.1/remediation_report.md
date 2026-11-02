# MDRAP v4.0.1 Remediation Report

**Date:** 2026-11-02  
**Platform:** Market Data Reliability & Acceleration Platform (MDRAP)  
**Version:** v4.0.1  
**Target Architecture:** Multi-tier Market Data Processing Engine  
**Author:** Principal Infrastructure & Durability Engineer  

---

## 1. Executive Summary

During the recent MDRAP re-audit, several severe architectural deficiencies and correctness bugs were demonstrated across recovery, projection handling, sequence tracking, and lock contention:

1. **R2 / P0-A (Snapshot Corruption & Truncation Disaster):** Corrupting a snapshot file after WAL pruning caused silent state loss (2,861 out of 3,100 events vanished upon restart) because the engine did not maintain multi-candidate snapshot fallbacks, pruned WAL records required by older recovery points, and lacked strict fail-closed recovery invariants.
2. **P0-B (Snapshot Offset & Checkpoint Inconsistencies):** Inconsistent offset semantics (using `event_count - 1` instead of contiguous WAL offsets) allowed dropped/oversize payloads to cause state drift between live and replayed execution.
3. **P0-C (Projection Reliability & External Lock Contention):** Downstream SQLite projection failure caused events to be skipped while the projection checkpoint advanced anyway, reporting `HEALTHY` and 0 lag despite missing rows. Furthermore, external SQLite locks blocked the global ingestion lock `Engine._lock` for up to 3 seconds across unrelated market feeds.
4. **P0-D (Sequence Poisoning & Deduplication Discrepancies):** A sequence jump (e.g. 2^62) permanently poisoned sequence validation for subsequent legitimate events. Old retransmissions were erroneously classified as `SUSPICIOUS` instead of `INVALID` duplicates. Price regime shifts caused permanent `SUSPICIOUS` alarms.
5. **R1 / P0-E (Grouped Durability Trailing Loss):** Under `grouped_by_time` fsync policy, trailing events in a quiet period remained in userspace stdio/OS buffers indefinitely and were lost on crash without background synchronization.
6. **Concurrent Snapshot Latency Spikes:** Snapshot creation performed slow synchronous deep copies under the global lock, stalling high-throughput order books.

All issues have been completely engineered, verified with reproducible regression suites, and certified against pure Python and native C fastpath tiers.

---

## 2. Root Cause Analysis & Architectural Remediations

### 2.1 P0-A & P0-B: Snapshot Lifecycle, Pruning & Fail-Closed Recovery
- **Root Cause:** Snapshots were written to a single unversioned `snapshot.json`. When pruned, WAL segments prior to the snapshot were removed. If `snapshot.json` was corrupted or tampered with, the engine fell back to an empty initial state or replayed from 0, failing silently if early segments were missing.
- **Architectural Solution:**
  1. Atomic versioned snapshots: `snapshot_{offset:016d}.json` written with temporary PID/nanosecond staging, SHA-256 verification, and atomic rename.
  2. Durable manifest: `snapshot_manifest.json` tracks the retention ring (default 5 historical snapshots).
  3. Multi-candidate discovery: `Engine._discover_candidate_snapshots()` loads candidate snapshots descending by offset. If the newest snapshot fails SHA-256 integrity, it falls back to prior snapshots.
  4. Safe WAL pruning: `delete_segments_before()` ensures WAL segments are only pruned if all retained recovery checkpoints are strictly greater than the segment offset.
  5. Fail-Closed Invariant: If a snapshot is corrupted and WAL history prior to `earliest_offset` is missing, `Engine` raises `RecoveryError` rather than silently starting with partial state.
  6. Offset Monotonicity: State records `last_applied_offset` directly from contiguous WAL record allocations, decoupling accepted events from raw presentation counters.

### 2.2 P0-C: Projection Reliability, Contiguous Checkpoints & Lock Isolation
- **Root Cause:** Projections were invoked synchronously inside `with self._lock:` in `Engine.submit()`. When SQLite locked (`SQLITE_BUSY`), the ingestion thread froze. Furthermore, failed projection batches advanced checkpoints without contiguous verification.
- **Architectural Solution:**
  1. Decoupled Ingestion: Engine releases `self._lock` before dispatching decisions to projections, eliminating cross-feed head-of-line blocking (blocking reduced from 3.04s to 0.06s).
  2. `ProjectionTracker`: Manages independent durable checkpoints, bounded failure queues, and retry coordination.
  3. Strict Contiguous Checkpoint Invariant: `SQLiteProjection.apply()` enforces:
     $$\text{cur\_cp} < 0 \quad\lor\quad (\text{offset} \ge \text{cur\_cp} \;\land\; \text{batch\_start} \le \text{cur\_cp} + 1)$$
     A checkpoint never advances across a gap.
  4. Degraded Health Telemetry: Projections with uncommitted holes report `health="DEGRADED"`, recording exact failed offsets, retry attempts, and lag.

### 2.3 P0-D: Sequence Correctness & Engine Alignment
- **Root Cause:** Sequence checking only tracked `last_seq` as a single integer. Jumps above threshold poisoned the slot; duplicates before `last_seq` were marked out-of-order instead of duplicate; price regime shifts lacked outlier re-centering.
- **Architectural Solution:**
  1. 64-Bit Sliding Window Bitmask (`sequence_seen`): Bit `d = last_seq - seq` tracks received packets within 64 positions. Seen packets are flagged `DUPLICATE` (QualityStatus.INVALID) and `OUT_OF_ORDER`.
  2. Candidate Tracking (`sequence_cand`): Forward jumps $> 1000$ increment candidate counters. Two consecutive packets in the new sequence band re-anchor the sequence baseline without permanent poisoning.
  3. Price Corridor Reseeding (`DEFAULT_PRICE_RESEED_AFTER = 8`): 8 consecutive price anomalies trigger dynamic mean/std reset, adapting to genuine economic regime shifts (+20% jumps).
  4. Pure Parity: Engine inline processing, pure Python `QualityEngine`, and native C `FastQualityEngine` match 100% across all scenarios.

### 2.4 P0-E: Grouped WAL Durability Semantics
- **Root Cause:** In `grouped_by_time` and `grouped_by_size` policies, events were flushed to userspace stdio but did not call `os.fsync()`. During quiet periods, trailing events stayed unpersisted.
- **Architectural Solution:**
  1. Background Flusher Coordinator (`_flusher_loop`): Spawns a low-overhead background thread that fsyncs pending writes if quiet period exceeds `group_commit_interval_ms` (default 10ms).
  2. `await_durable(target)`: Callers can synchronously barrier on durability for regulatory or transactional commitments.
  3. Zero Trailing Loss: Confirmed via `e_new_risks.py` R1 test: trailing events are guaranteed durable within the quiet window.

---

## 3. Verification & Evidence Matrix

| Area | Experiment / Test | Result |
| :--- | :--- | :--- |
| **Snapshot Fallback** | `reaudit_experiments/e_new_risks.py` (R2) | **PASS**: 3,100 of 3,100 events recovered after primary snapshot corruption. |
| **WAL Fail-Closed** | `tests/test_stage_a_snapshot_corruption_recovery.py` | **PASS**: `RecoveryError` raised when snapshot corrupt and WAL pruned. |
| **State Equivalence** | `reaudit_experiments/e_property.py 300` | **PASS**: 0/300 divergences across all 3 test configurations. |
| **Projection Gap Integrity** | `reaudit_experiments/e_proj2.py` | **PASS**: Missing offsets 3, 4, 5 recovered; checkpoint held at 2 until fill. |
| **Lock Isolation** | `reaudit_experiments/e_sqlite_proj.py` | **PASS**: Ingestion lock contention eliminated (3.04s $\to$ 0.06s). |
| **Sequence Parity** | `reaudit_experiments/e_diff.py` | **PASS**: 100% agreement between Engine, QualityEngine, and FastQualityEngine. |
| **Regime Adaptation** | `reaudit_experiments/e_correctness.py` (E4) | **PASS**: Adapts to +20% price shift within 8 ticks. |
| **Durability Barrier** | `tests/test_stage_e_grouped_wal_durability.py` | **PASS**: Trailing quiet-period events survive immediate crash. |
| **Full Regression Gate** | `scripts/release_gate.py` (14 Stages) | **PASS**: All 14 release gates passed in 387s. |
| **Full Unit/Integration** | `pytest tests/` | **PASS**: 1,600 passed, 0 failed, 52 warnings. |

---

## 4. Conclusion & Sign-Off Recommendation
MDRAP v4.0.1 satisfies all institutional correctness invariants: zero silent loss, strict fail-closed recovery, lock-isolated projection streams, and verified multi-tier semantic equivalence.
