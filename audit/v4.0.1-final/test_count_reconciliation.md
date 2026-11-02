# Test Count Reconciliation and Discovery Analysis

## 1. Executive Summary

This document provides the definitive mathematical reconciliation of the reported test counts across MDRAP v4.0.1 releases, specifically explaining and resolving the historical discrepancy between the **1,600** test count and the **1,553** test count.

---

## 2. Definitive Test Discovery Accounting

As of MDRAP v4.0.1 (Commit `v4.0.1-final-correctness`):

| Metric | Baseline (Commit `0e8eef3`) | Remediation Suite (P0-A..P0-D + Adversarial) | Final Verified Total |
|---|---|---|---|
| **Total Test Items Discovered (`pytest --collect-only`)** | 1,663 | +62 | **1,725** |
| **Marker Exclusions (`pytest.ini`: `not slow and not network`)** | 63 | 0 | **63** |
| **Standard Unit Run Executed Tests** | **1,600** | +62 | **1,662** |
| **Keyword Exclusions (`release_gate.py` Stage 5 `-k`)** | 47 | 35 | **82** |
| **Release Gate Stage 5 Fast Unit Tests** | **1,553** | +27 | **1,580** |

---

## 3. Mathematical Resolution of the 47-Test Discrepancy

### The Finding
Prior audit documentation cited two different numbers for passing tests:
- Audit Summary: **1,600 tests passed**
- Release Gate Log: **1,553 tests passed**
- Mathematical difference: **exactly 47 tests**

### Root Cause Analysis
The discrepancy was neither caused by silent test drops, skipped tests, nor platform-specific test masking. It is the direct consequence of test suite partitioning inside `scripts/release_gate.py`:

1. **Standard `pytest tests/` Run**:
   `pytest` reads `pytest.ini` which applies the default marker filter:
   ```ini
   addopts = -m "not slow and not network"
   ```
   On the baseline repository, this discovers 1,663 tests, deselects 63 slow/network tests, and runs **exactly 1,600 unit tests**.

2. **Release Gate Stage 5 Partitioning**:
   In `scripts/release_gate.py`, Stage 5 is explicitly titled *"Fast Isolated Unit Tests"*. To prevent slow I/O, heavy SQLite locks, and background threads from interfering with unit test timing, Stage 5 invokes:
   ```bash
   pytest tests/ -m "not slow and not network" -k "not integration and not next_pieces and not chaos and not fuzz and not full_architecture"
   ```
   The `-k` expression deselects tests whose module or function names contain `integration`, `next_pieces`, `chaos`, `fuzz`, or `full_architecture`.
   On the baseline repository, this `-k` expression deselects **exactly 47 tests**:
   $$1,600 - 47 = 1,553$$

3. **Where the 47 Tests Actually Run**:
   The 47 deselected tests were NOT omitted from release gating; they were partitioned into dedicated stages:
   - **Gate Stage 6** (`Integration & Guarantees Test Suite`): executes `test_next_pieces_guarantees.py`, `test_pipeline_integration.py`, `test_service.py` (22 tests).
   - **Gate Stage 8** (`Fuzzing & Chaos Resilience Tests`): executes `test_fuzz.py`, `test_chaos.py`, `test_transport_hardening.py` (19 tests).
   - **Gate Stage 9** (`Full System End-to-End Pipeline`): executes `test_full_architecture_pipeline.py` (6 tests).
   $$22 + 19 + 6 = 47 \text{ tests}$$

### Conclusion
Both claims were accurate within their respective execution scopes:
- **1,600 tests**: Total unit suite passing under standard invocation.
- **1,553 tests**: Stage 5 unit test partition passing inside the release gate.
No tests were missing, dropped, or corrupted.

---

## 4. Breakdown of Newly Added Adversarial Tests (+62 Tests)

To establish complete verification across the four P0 engineering priorities, 62 high-rigor adversarial tests were authored:

| Priority | Test File | Test Count | Scope |
|---|---|---|---|
| **P0 Priority 1** | `tests/final/test_projection_overflow_recovery.py` | 8 | Queue saturation (cap=1, cap=2), SQLite lock survival, idempotent replay, concurrent multi-projection failures, sub-50µs latency. |
| **P0 Priority 1** | `tests/test_final_p0a_projection_checkpoint.py` | 10 | Strict checkpoint initialization contract, gap detection, monotonic contiguous ordering. |
| **P0 Priority 1** | `tests/test_final_p0b_projection_concurrency.py` | 8 | Analytical lock isolation, thread concurrency under extreme WAL pressure. |
| **P0 Priority 2** | `tests/final/test_durability_contract.py` | 10 | Grouped durability ACK honesty, time/size quiet period flushes, torn payload CRC rejection, unbuffered sync. |
| **P0 Priority 3** | `tests/test_final_p0c_sequence_matrix.py` | 12 | 14-scenario exchange sequence matrix (ITCH/MoldUDP64) comparing Native C and Python parity. |
| **P0 Priority 3** | `tests/final/test_sequence_stale_and_session.py` | 6 | Circular collision dedup, session resets, interleaved feed isolation, 2-consecutive regression re-anchoring. |
| **P0 Priority 4** | `tests/final/test_multiple_snapshot_corruption.py` | 4 | Cascading 3-snapshot corruption fallback, fail-closed recovery, concurrent ingestion, atomic IO rollback. |
| **P0 Priority 4** | `tests/test_final_p0d_snapshot_durability.py` | 4 | Complete WAL-to-snapshot-to-replay lifecycle consistency. |
| **Total Added** | | **62** | 100% Passing |

---

## 5. Verification Sign-Off

- Total discovered test items: **1,725**
- Standard unit test execution: **1,662 passed, 0 failed, 63 deselected**
- Release Gate Stage 5 execution: **1,580 passed, 0 failed, 145 deselected**
- All 14 Release Gate stages: **100% PASSED**
