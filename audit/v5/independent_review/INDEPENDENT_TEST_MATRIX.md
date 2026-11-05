# MDRAP v5.0 — Independent Test Execution Matrix

**Audit Date**: 2026-11-05  
**Auditor**: Independent QA & Verification Engineer  
**Classification**: `[TEST-MATRIX]`

---

## 1. Test Suite Execution Summary

| Test Domain / Gate | Target Path / Marker | Tests Collected | Tests Passed | Tests Failed | Tests Skipped | Deselected | Execution Duration |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Gate 01: Code Formatting** | `ruff format --check src/` | 219 files | 219 | 0 | 0 | 0 | 0.22s |
| **Gate 02: Static Linting** | `ruff check src/` | 219 files | 219 | 0 | 0 | 0 | 0.20s |
| **Gate 03: Rule Sync** | `tools/gen_reasons.py --check` | 15 rules | 15 | 0 | 0 | 0 | 0.20s |
| **Gate 04: Native C Build** | `build_fastpath.py` | 3 binaries | 3 | 0 | 0 | 0 | 9.06s |
| **Gate 05: Core Unit Tests** | `tests/ -m "not slow and not network"` | 1,735 | **1,672** | **0** | 0 | 63 | 350.79s |
| **Gate 06: Integration Tests** | `tests/test_next_pieces_guarantees.py` etc. | 23 | **23** | **0** | 0 | 0 | 16.97s |
| **Gate 07: Security Tests** | `tests/test_security.py` etc. | 30 | **30** | **0** | 0 | 0 | 10.33s |
| **Gate 08: Fuzzing & Chaos** | `tests/test_fuzz.py`, `test_chaos.py` etc. | 15 | **15** | **0** | 0 | 0 | 14.39s |
| **Gate 09: End-to-End** | `tests/test_full_architecture_pipeline.py` | 1 | **1** | **0** | 0 | 0 | 6.68s |
| **Gate 9b: Adversarial Suite**| `tests/final/` | 29 | **29** | **0** | 0 | 0 | 17.66s |
| **Gate 10: Dependency CVE** | `gate_dependency_hygiene()` | 3 specs | 3 | 0 | 0 | 0 | 0.11s |
| **Gate 11: CLI Diagnostics** | `python -m mdrap.cli doctor --json` | 1 check | 1 | 0 | 0 | 0 | 1.49s |
| **Gate 12: Micro-FFI Smoke** | `benchmarks/micro_ffi.py` | 1 run | 1 | 0 | 0 | 0 | 1.85s |
| **Gate 13: Regression Guard**| `benchmarks/check_regression.py` | 4 metrics | 4 | 0 | 0 | 0 | 2.06s |
| **Gate 14: Packaging Audit** | `scripts/verify_release_artifacts.py` | 6 artifacts | 6 | 0 | 0 | 0 | 85.42s |
| **TOTALS** | **Authoritative 14-Stage Gate** | **1,735 Repo Nodes** | **1,770 Executions** | **0** | **0** | **63** | **517.43s (~8.6m)** |

---

## 2. Targeted Audit Experiment Matrix

The audit executed a dedicated suite of targeted adversarial and property-based verification tests:

| Experiment Name | Test File | Commands & Parameters | Purpose & Verification Target | Observed Evidence | Verdict |
|:---|:---|:---|:---|:---|:---:|
| **Differential Parity** | `tests/test_parity_differential.py` | `pytest tests/test_parity_differential.py -v` (5 seeds: 42, 1337, 777, 2026, 99999) | Bit-identical status & reasons across Python and C fastpath across 10,000 fault-injected events | 5/5 passed; 100% agreement on status, reasons, and counts | **PASS** |
| **Real Kill-9 Recovery** | `tests/test_gate_g2_kill9.py` | `pytest tests/test_gate_g2_kill9.py -v` (25 randomized subprocess terminations) | Zero acknowledged event loss after abrupt OS-level kill during ingestion | 25/25 runs passed; 0 lost acked events, 0 duplicate conflicts | **PASS** |
| **SPSC Queue Stress** | `tests/test_channel_sequencing_partition.py` | `pytest tests/test_channel_sequencing_partition.py -v` | Producer/consumer transfer integrity under high concurrency | 7/7 passed; zero dropped elements or order inversions | **PASS** |
| **SHM Seqlock Adversarial** | `tests/test_shm_fuzz.py`, `tests/test_shm_publisher_crash.py` | `pytest tests/test_shm_fuzz.py tests/test_shm_publisher_crash.py -v` | Detection of torn writes, slot overruns, and dead publisher restarts | 16/16 passed; seqlock safely rejects torn reads and handles crash | **PASS** |
| **Durability Contracts** | `tests/final/test_durability_contract.py` | `pytest tests/final/test_durability_contract.py -v` | Explicit contract strings (`always`, `never`, `grouped`) and quiet-period flush | 10/10 passed; durable=False truthful under `never`; always fsynced | **PASS** |
| **Torn Tail Truncation** | `tests/test_ingestlog.py` | `pytest tests/test_ingestlog.py -v` | Truncation of incomplete frame headers and bitflip CRC mismatches | 5/5 passed; torn tails cleanly truncated; bitflips poisoned safely | **PASS** |
| **Pure Python Fallback** | `tests/test_fastpath.py` | `$env:MDRAP_DISABLE_FASTPATH="1"; pytest tests/test_fastpath.py -v` | Verify system functions correctly when native C DLL is disabled | 14/14 passed cleanly in pure-Python fallback mode | **PASS** |
| **12-Tier Benchmark Suite** | `audit/v5/performance/REPRODUCTION_SCRIPTS/run_12_benchmarks.py` | `python run_12_benchmarks.py` (682,000 events) | Independent throughput and tail latency measurement across all tiers | WAL append reaches 69.6k EPS; SPSC reaches 1.68M EPS | **PASS** |

---

## 3. Reproduction Commands

To reproduce the complete audit verification run identically on another machine:

```powershell
# 1. Verify single-source rules synchronization
python tools/gen_reasons.py --check

# 2. Compile native C accelerators
python build_fastpath.py

# 3. Execute targeted correctness & durability tests
pytest tests/test_parity_differential.py tests/test_gate_g2_kill9.py tests/final/test_durability_contract.py -v

# 4. Execute full 14-stage authoritative release gate
python scripts/release_gate.py

# 5. Run claims governance scanner
python scripts/claims_scanner.py --check

# 6. Execute 12-tier empirical performance benchmark
python audit/v5/performance/REPRODUCTION_SCRIPTS/run_12_benchmarks.py
```

---

## 4. Test Matrix Sign-Off

The test suite exhibits 100% pass rates across all active test nodes. All 1,770 release gate executions completed with zero failures or unexpected skips.
