# MDRAP v4.0.1 — Remaining Defects and Remediated Findings

**Audit Date:** 2026-11-04  
**Auditor:** Principal Systems Engineer & Independent Release Auditor

---

## 1. Summary of Discovered Findings

| Finding ID | Title | Severity | Confidence | Status | Release Blocking |
|---|---|:---:|:---:|:---:|:---:|
| **SQ-01** | C vs Python Stale Seen Buffer Capacity Mismatch (4 vs 64) | **P0** | Confirmed | **RESOLVED** | Yes (Was Blocking) |
| **D-01** | `IngestLog` Rejection of Documented Policy Names (`every_event`, `batch`, `adaptive`) | **P1** | Confirmed | **RESOLVED** | No |
| **SN-01** | Missing `CorruptedSnapshotError` Exception Class | **P1** | Confirmed | **RESOLVED** | No |
| **BM-01** | In-Memory Microbenchmark vs End-to-End Pipeline Latency Conflation | **P2** | Confirmed | **DOCUMENTED** | No |
| **S-01** | Outdated Gate 5 Test Counts in `claims_manifest.json` (1553 vs 1578) | **P2** | Confirmed | **RECONCILED** | No |

---

## 2. Detailed Finding Analysis

### Finding SQ-01: C vs Python Stale Seen Buffer Capacity Mismatch
- **Severity:** P0 (Core Parity Violation)
- **Impact:** Under an adversarial stream of 5 distinct stale sequence packets, Native C evicted slot 0 on the 5th packet, failing to flag its subsequent retransmission as a duplicate (`SUSPICIOUS (OUT_OF_ORDER)`), while Python flagged it as `INVALID (DUPLICATE, OUT_OF_ORDER)`.
- **Resolution:** Modified [`src/mdrap/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/quality.py#L533) to align Python's `_SlotState.stale_seen` capacity to 4 entries, matching `FastSlot.stale_seen[4]`. Verified with `test_p0_parity_stale_capacity.py`.

---

### Finding D-01: IngestLog Policy Alias Rejection
- **Severity:** P1 (API Compatibility)
- **Impact:** Callers passing documented policy names `every_event`, `batch`, or `adaptive` encountered `ValueError: Invalid fsync_policy`.
- **Resolution:** Added `POLICY_ALIASES` in [`src/mdrap/ingestlog.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ingestlog.py#L380) mapping `every_event` -> `always`, `batch` -> `grouped_by_time`, `adaptive` -> `true_group_commit`.

---

### Finding SN-01: Undefined `CorruptedSnapshotError`
- **Severity:** P1 (Exception Hygiene)
- **Impact:** Callers catching `CorruptedSnapshotError` encountered `NameError` or `ImportError`.
- **Resolution:** Defined `class CorruptedSnapshotError(RecoveryError): pass` in [`src/mdrap/models.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/models.py#L38) and exported in [`src/mdrap/__init__.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/__init__.py#L86).

---

### Finding BM-01: In-Memory vs End-to-End Pipeline Conflation
- **Severity:** P2 (Documentation Transparency)
- **Impact:** Marketing and architectural materials claimed 47k-55k EPS without clarifying that this figure represents the in-memory state transition (`engine.step`). The full end-to-end pipeline with on-disk WAL appends and synchronous SQLite projections achieves ~2.0k – 2.5k EPS on consumer NVMe storage.
- **Resolution:** Fully documented in `BENCHMARK_REPRODUCTION.md` and `RELEASE_DECISION.md`.
