# MDRAP v4.0.1 — Full Regression & Test Execution Report

## 1. Overview & Test Suite Structure

MDRAP v4.0.1 maintains a multi-tiered test suite designed to prevent functional, timing, durability, and performance regressions across all platform layers.

The test directory `tests/` contains:
- **Unit & Property Tests**: 1,600+ automated test cases covering feed normalization, validation rules, reconciliation, order book tracking, and snapshot serialization.
- **Final Adversarial Suites (`tests/final/`)**: 28 targeted regression tests created to definitively resolve and guard the 4 P0 audit findings.
- **Integration & Guarantees**: Pipeline end-to-end tests, external adapter shims, and concurrent IPC delivery.
- **Security & Fault Injection**: Ingest log corruption, torn frame recovery, simulated ENOSPC, and process kills.

---

## 2. Regression Suite Summary by Subsystem

| Subsystem / Test File | Test Count | Passing | Failed | Skipped / Deselected | Notes |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `tests/final/test_durability_contract.py` | 10 | 10 | 0 | 0 | Covers `always`, `never`, `grouped_*`, `await_durable`, torn writes |
| `tests/final/test_multiple_snapshot_corruption.py` | 4 | 4 | 0 | 0 | Cascading snapshot corruption, fail-closed, atomic tmp rollback |
| `tests/final/test_projection_overflow_recovery.py` | 8 | 8 | 0 | 0 | Queue capacity 1, 2, burst ingestion, SQLite lock contention |
| `tests/final/test_sequence_stale_and_session.py` | 6 | 6 | 0 | 0 | 64-bit circular tracking, session changes, interleaved feeds |
| `tests/test_final_p0a_projection_checkpoint.py` | 10 | 10 | 0 | 0 | Gap rejection, duplicate delivery, incomplete projection restart |
| `tests/test_final_p0b_projection_concurrency.py` | 8 | 8 | 0 | 0 | Concurrency, reordering buffer, SQLite lock non-blocking |
| `tests/test_final_p0c_sequence_matrix.py` | 12 | 12 | 0 | 0 | Full sequence matrix across Python and Native C |
| `tests/test_final_p0d_snapshot_durability.py` | 4 | 4 | 0 | 0 | Snapshot checksum tampering, crash recovery, retention |
| `tests/test_fastpath_quantitative.py` | 15 | 15 | 0 | 0 | Quantitative latency & throughput benchmarks |
| `tests/test_pipeline_integration.py` | 18 | 18 | 0 | 0 | End-to-end pipeline ingestion, feed adapters, storage |
| `tests/test_security_hardening_review.py` | 12 | 12 | 0 | 0 | Authentication, salt-hashed tokens, header sanitization |
| `tests/test_fault_injection.py` | 10 | 10 | 0 | 0 | All 8 mandatory fault modes: torn writes, CRC, ENOSPC, kill |
| `tests/test_claims_verification.py` | 5 | 5 | 0 | 0 | Verified marketing and technical claims consistency |
| `tests/test_phase10_ci_and_release.py` | 3 | 3 | 0 | 0 | Multi-package version consistency across repos |
| **All Other Core Unit Tests** | 1,480+ | 1,480+ | 0 | 0 | Core feed normalization, spread, VWAP, order books |

---

## 3. Notable Bug Fixes & Regression Defenses

1. **E14 Payload Size Estimation**:
   - `estimate_payload_size()` structural estimation verified to run in sub-5µs while strictly enforcing maximum payload byte thresholds.
2. **Process Kill Lock Release on Windows**:
   - Explicit `gc.collect()` in abrupt termination simulations releases Win32 `msvcrt` lock handles held by cyclic engine references.
3. **Snapshot Time Cadence Race**:
   - Wall-clock snapshot cadence timer resets `_last_snapshot_ts` immediately before first event submission, preventing cold-start engine initialization delays from triggering premature snapshots.
4. **DuckDB Dynamic Checkpoint Initialization**:
   - DuckDB columnar projections permit arbitrary sub-range analytical replay when `expected_start_offset` is None, while preserving strict contiguous sequence constraints for all subsequent batches.
