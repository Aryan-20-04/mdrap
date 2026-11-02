# MDRAP v4.0.1 Test Evidence Log

**Test Run Timestamp:** 2026-11-02 15:15 UTC  
**Environment:** Windows amd64, Python 3.13.1, GCC 14 (MinGW-w64)  
**Test Suite Total:** 1,600 tests passed, 0 failed, 52 warnings  

---

## 1. Authoritative 14-Stage Release Gate Results

Executed command: `python scripts/release_gate.py`  
Status: **ALL 14 CONFIGURED RELEASE GATES PASSED (387.32s)**

```
============================================================================
                 MDRAP 14-STAGE AUTHORITATIVE RELEASE GATE                  
============================================================================

[RELEASE GATE 01/14] Code Formatting (ruff format --check src/)...
[PASSED] Release Gate 01: Code Formatting (ruff format --check src/) (0.26s)

[RELEASE GATE 02/14] Static Analysis (ruff check src/)...
[PASSED] Release Gate 02: Static Analysis (ruff check src/) (0.22s)

[RELEASE GATE 03/14] Single-Source Rule Parity...
[PASSED] Release Gate 03: Single-Source Rule Parity (0.18s)

[RELEASE GATE 04/14] Native C Fastpath Compilation...
[build] SUCCESS! Compiled _fastpath_native.dll (200,419 bytes)
[build] SUCCESS! Compiled mdrap-core.exe (379,924 bytes)
[build] SUCCESS! Compiled _fastpath_c.cp313-win_amd64.pyd (216,213 bytes)
[PASSED] Release Gate 04: Native C Fastpath Compilation (15.73s)

[RELEASE GATE 05/14] Fast Isolated Unit Tests...
[PASSED] Release Gate 05: Fast Isolated Unit Tests (239.37s) (1,518 passed)

[RELEASE GATE 06/14] Integration & Guarantees Test Suite...
[PASSED] Release Gate 06: Integration & Guarantees Test Suite (17.65s) (23 passed)

[RELEASE GATE 07/14] Security Hardening & Key Storage Tests...
[PASSED] Release Gate 07: Security Hardening & Key Storage Tests (10.59s) (30 passed)

[RELEASE GATE 08/14] Fuzzing & Chaos Resilience Tests...
[PASSED] Release Gate 08: Fuzzing & Chaos Resilience Tests (12.92s) (10 passed)

[RELEASE GATE 09/14] Full System End-to-End Pipeline...
[PASSED] Release Gate 09: Full System End-to-End Pipeline (6.89s)

[RELEASE GATE 10/14] Dependency CVE Hygiene...
Installed pyarrow runtime verified: 23.0.1
Dependency constraints in requirements.txt & pyproject.toml meet CVE safety thresholds.
[PASSED] Release Gate 10: Dependency CVE Hygiene (0.11s)

[RELEASE GATE 11/14] Platform Diagnostics & Doctor...
[PASSED] Release Gate 11: Platform Diagnostics & Doctor (1.07s)

[RELEASE GATE 12/14] Native Accelerator Smoke Check...
[PASSED] Release Gate 12: Native Accelerator Smoke Check (1.40s)

[RELEASE GATE 13/14] Performance Regression Protection Guard...
Throughput (EPS): 53,369.3 (Baseline: 34,489.0 -> +54.7%) [PASS]
Latency p50: 16.10 us (Baseline: 18.80 us -> -14.4%) [PASS]
Latency p95: 23.80 us (Baseline: 60.40 us -> -60.6%) [PASS]
Latency p99: 61.90 us (Baseline: 107.90 us -> -42.6%) [PASS]
[PASSED] Release Gate 13: Performance Regression Protection Guard (1.49s)

[RELEASE GATE 14/14] Documentation & Release Artifact Verification...
[PASSED] Release Gate 14: Documentation & Release Artifact Verification (79.44s)
```

---

## 2. Re-Audit Experiment Verification Suite

| Script | Test Description | Evidence / Output | Status |
| :--- | :--- | :--- | :--- |
| `e_correctness.py` | E1: Oversize payload accounting<br>E2: Sequence poisoning resilience<br>E3: Old retransmission deduplication<br>E4: Price corridor regime adaptation<br>E5: Dedup fingerprint ignores book sizes<br>E6: Replay purity without gap side-effects | All 6 scenarios passed. Live state equals recovered state. Retransmissions rejected as INVALID duplicates. Regime shift adapts after 8 ticks. | **PASS** |
| `e_diff.py` | Multi-tier engine divergence: Engine inline vs QualityEngine(py) vs FastQualityEngine(native C) | 100% agreement across all 4 scenarios (Poisoned sequence, Retransmit old sequence, Regime shift, Late gap fill). | **PASS** |
| `e_new_risks.py` | R1: Grouped-by-time quiet period fsync<br>R2: Snapshot corruption with pruned WAL | R1: Unsynced ACKed events: `False`.<br>R2: 3,100 of 3,100 events recovered without silent loss. | **PASS** |
| `e_proj2.py` | Flaky projection outage recovery | Offsets 3..7 caught up cleanly. Checkpoint advanced only after full contiguous fill. | **PASS** |
| `e_sqlite_proj.py` | External SQLite writer lock contention | Unrelated feed submit time during 3s DB lock: **0.06s** (head-of-line blocking eliminated). | **PASS** |
| `e_snap_concurrent.py` | Concurrent submit latency during 10k snapshot | 1,804 submits completed during 1.9s snapshot. Max latency: 114ms (down from unbounded stall). | **PASS** |
| `e_bytes.py` | Raw bytes payload append and replay | Valid bytes serialized to hex, non-serializable objects raise TypeError cleanly. | **PASS** |
| `e_property.py` | Randomized stateful test across all operations (oversize, snapshot, restart) | **0 / 300 divergences** across all 3 configurations. | **PASS** |

---

## 3. Dedicated Stage Test Suite

Executed command:
`pytest tests/test_stage_a_snapshot_corruption_recovery.py tests/test_stage_c_projection_recovery.py tests/test_stage_d_sequence_and_quality_alignment.py tests/test_stage_e_grouped_wal_durability.py -v`

Results: **13 passed in 5.48s**
- `test_corrupt_latest_snapshot_falls_back_to_previous_valid_snapshot`: PASSED
- `test_corrupt_all_snapshots_with_pruned_wal_fails_closed`: PASSED
- `test_corrupted_manifest_falls_back_to_directory_scan`: PASSED
- `test_interrupted_snapshot_tmp_files_ignored`: PASSED
- `test_snapshot_rotation_and_retention_limit`: PASSED
- `test_projection_checkpoint_strictly_contiguous`: PASSED
- `test_sqlite_projection_lock_isolation_across_feeds`: PASSED
- `test_projection_metrics_reporting`: PASSED
- `test_sequence_jump_does_not_poison_subsequent_events`: PASSED
- `test_old_retransmission_is_marked_duplicate`: PASSED
- `test_genuine_regime_shift_adapts_price_corridor`: PASSED
- `test_grouped_by_time_quiet_period_fsync`: PASSED
- `test_grouped_by_time_reopened_retains_trailing_events`: PASSED

---

## 4. Pure Python Fallback Parity

Executed command:
`$env:MDRAP_DISABLE_FASTPATH="1"; pytest tests/test_stage_*.py -v`
Results: **13 passed in 6.31s**  
All validation and recovery logic operates identically with native C libraries unlinked.
