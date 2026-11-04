# MDRAP v4.0.1 — Snapshot Retention and Crash-Safe Pruning Audit

**Audit Date:** 2026-11-04  
**Target Subsystems:** `src/mdrap/engine.py`, `src/mdrap/models.py`  
**Raw Test Evidence:** `audit/v4.0.1-final-independent/raw_evidence/p0_snapshot_suite.log`

---

## 1. Crash Invariant Under Audit

The core institutional invariant audited is:
> **MDRAP must never delete the only recoverable representation of committed state.**

Under R2 re-audit experiments, WAL pruning was previously aggressive, purging all segments prior to the latest snapshot offset. If that snapshot suffered bit flips, torn writes, or checksum tampering, recovery failed with massive data loss (2,861 events missing).

---

## 2. Retention Architecture and Multi-Candidate Fallback

The remediated architecture in [`src/mdrap/engine.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/engine.py#L2650-L3020) implements:
1. **Multi-Generational Snapshot Files:** Up to `max_snapshots_retained=3` (or 5) versioned files (`snapshot_{offset}_{ts}.json`) are preserved.
2. **Crash-Resilient Safe Prune Offset:**
   ```python
   if min_wal_snapshots_retained > 0 and len(candidates_after) > 1:
       target_idx = min(min_wal_snapshots_retained, len(candidates_after) - 1)
       fallback_offset = candidates_after[target_idx][0]
       safe_prune_offset = fallback_offset + 1
   else:
       safe_prune_offset = last_offset + 1
   ```
   WAL segments are retained back to the fallback snapshot offset, guaranteeing that even if the newest snapshot is unreadable or corrupted, the system can load the previous valid snapshot and replay retained WAL segments forward to the head.
3. **Atomic Publication & Directory Fsync:**
   Snapshots are serialized, flushed, and fsynced to a `.tmp` file, verified by reading back SHA-256 before being published via `os.replace()` and `_fsync_dir()`.

---

## 3. Discrepancies and Remediations

### Finding SN-01: Undefined `CorruptedSnapshotError` Exception Class
- **Severity:** P1 (Exception Hierarchy & Error Handling)
- **Confidence:** Confirmed
- **Evidence:** Audit documentation and reference guides refer to `CorruptedSnapshotError`, but searching `src/mdrap/` revealed it was never declared. In `src/mdrap/engine.py`, snapshot failures directly raised generic `RecoveryError` or `RuntimeError`.
- **Remediation Implemented:**
  1. Declared `CorruptedSnapshotError` as a direct subclass of `RecoveryError` in [`src/mdrap/models.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/models.py#L38):
     ```python
     class CorruptedSnapshotError(RecoveryError):
         """Raised when snapshot verification fails SHA-256 integrity, checksum validation, or file structure checks."""
         pass
     ```
  2. Exported in [`src/mdrap/__init__.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/__init__.py#L86).
  3. Raised in [`src/mdrap/engine.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/engine.py#L667) when all candidate snapshots fail checksum validation and unrecoverable WAL history prevents full replay.
- **Verification:** Preserves 100% exception compatibility; callers catching `RecoveryError` continue catching it, while callers targeting `CorruptedSnapshotError` now work as documented.

---

## 4. Fault-Injection & Corruption Matrix Results

The corruption matrix executed via [`tests/final/test_multiple_snapshot_corruption.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/final/test_multiple_snapshot_corruption.py) and [`tests/test_final_p0d_snapshot_durability.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_final_p0d_snapshot_durability.py) (8/8 passed):

| Failure Scenario | Injected Fault | System Behavior | Data Loss | Verdict |
|---|---|---|:---:|:---:|
| Newest Snapshot Corrupted | SHA-256 payload bytes flipped | Falls back to snapshot $N-1$, replays retained WAL to head | **0 events** | **PASS** |
| Process Crash Mid-Write | Abrupt kill before rename | Incomplete `.tmp` ignored; previous snapshots & WAL intact | **0 events** | **PASS** |
| Process Crash Mid-Prune | Abrupt kill during deletion | Remaining segments and fallback snapshots provide valid recovery | **0 events** | **PASS** |
| All Retained Snapshots Corrupted | SHA-256 flipped on all snapshots | Fails closed, raises `CorruptedSnapshotError` / `RecoveryError` | **0 partial state** | **PASS** |
| Required WAL History Missing | Segment deleted before earliest fallback | Fails closed with unrecoverable history error | **0 silent loss** | **PASS** |
