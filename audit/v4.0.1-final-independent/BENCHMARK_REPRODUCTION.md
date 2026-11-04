# MDRAP v4.0.1 — Benchmark Reproduction & Performance Audit

**Audit Date:** 2026-11-04  
**Auditor:** Principal Systems Engineer & Independent Release Auditor  
**Hardware Environment:**
- **CPU:** 12th Gen Intel(R) Core(TM) i7-1255U (10 cores: 2 P-cores, 8 E-cores, 12 logical processors, base 1.70 GHz, turbo 4.70 GHz)
- **RAM:** 16.0 GB DDR4/DDR5
- **OS:** Windows 11 Enterprise (Build 26100) x86_64
- **Python:** CPython 3.13.1 (tags/v3.13.1:0671451)
- **C Compiler:** Microsoft Visual C++ Compiler 19.42 (MSVC x64), optimization `/O2`

---

## 1. Executive Summary & Benchmark Integrity Finding

An essential responsibility of an independent release auditor is to distinguish between **isolated microbenchmarks** and **real end-to-end production pipeline performance**.

### Finding BM-01: Microbenchmark vs End-to-End Pipeline Conflation
- **Severity:** P2 (Documentation Transparency)
- **Confidence:** Confirmed
- **Evidence:** [`benchmarks/check_regression.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/benchmarks/check_regression.py#L82) executes:
  ```python
  state, _ = engine.step(state, ev, clock, offset=0)
  ```
  It measures the in-memory state fold *only* (normalization + quality validation + BBO synthesis). It does **NOT** write to the WAL on disk (`IngestLog.append`), compute frame CRC32s, execute fsyncs, or commit to SQLite projections.
- **Auditor Measurement:**
  When measuring the **full production path** (`engine.submit()` with active on-disk WAL append, CRC32C hashing, and synchronous SQLite projection persistence via [`benchmark_full_production_path.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/reproduction_scripts/benchmark_full_production_path.py)), the measured throughput is **~2,509 EPS** (under `fsync_policy="never"`) and **~1,957 EPS** (under `fsync_policy="grouped_by_time"`).
- **Conclusion:** Both numbers are legitimate, but they measure fundamentally different boundaries:
  - **In-Memory Core Engine Step:** ~49,500 EPS (16.20 µs latency).
  - **Pure C Fastpath Kernel:** 12.34 ns (~81,000,000 EPS).
  - **Full Pipeline with On-Disk WAL + SQLite:** ~2,000 – 2,500 EPS (248 – 367 µs latency).

---

## 2. Quantitative Benchmark Results

### A. Core Engine In-Memory Regression Benchmark (`benchmarks/check_regression.py`)
- **Dataset:** 30,000 deterministic quote and trade events.
- **Warmup:** 2,000 events.
- **Raw Evidence:** [`raw_evidence/benchmark_regression_independent.log`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/raw_evidence/benchmark_regression_independent.log)

| Metric | Measured Value | Baseline Reference | Delta vs Baseline | Gate Status |
|---|---|---|---|---|
| **Throughput (EPS)** | **49,596.1 EPS** | 34,489.0 EPS | **+43.8%** | **PASS** |
| **Latency p50 (µs)** | **16.20 µs** | 18.80 µs | **-13.8%** | **PASS** |
| **Latency p95 (µs)** | **45.00 µs** | 60.40 µs | **-25.5%** | **PASS** |
| **Latency p99 (µs)** | **79.60 µs** | 107.90 µs | **-26.2%** | **PASS** |

---

### B. ctypes FFI & Native Quality Kernel Deconstruction (`benchmarks/micro_ffi.py`)
- **Dataset:** 100,000 iterations × 3 runs.
- **Raw Evidence:** [`raw_evidence/benchmark_micro_ffi_independent.log`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/raw_evidence/benchmark_micro_ffi_independent.log)

| Execution Stage | Latency | Equivalent Throughput |
|---|---|---|
| Python Loop Overhead Baseline | 15.18 ns | ~65,800,000 EPS |
| **Pure C Quality Kernel** (net of FFI) | **12.34 ns** | **~81,000,000 EPS** |
| Single Scalar Argument C Call | 407.46 ns | ~2,450,000 EPS |
| Struct-by-Pointer Marshaling (1 arg) | 475.14 ns | ~2,100,000 EPS |
| Bare CDLL Call (12 scalar args) | 955.65 ns | ~1,046,000 EPS |
| Pinned CDLL Call (12 scalar args) | 2,209.00 ns | ~452,000 EPS |
| Full Fastpath FFI + C Evaluation | 2,221.34 ns | ~450,000 EPS |

*Analysis:* Pinned 12-scalar argument marshaling across the CPython FFI boundary contributes 99.4% of the latency (2,209 ns), while the native C quality logic itself executes in 12.34 ns. Switching to struct-by-pointer reduces marshaling overhead by 4.65x.

---

### C. Full End-to-End Pipeline Benchmark (`benchmark_full_production_path.py`)
- **Dataset:** 5,000 events (500 warmup, 4,500 timed).
- **Subsystems Active:** Normalization, Quality Engine, BBO synthesis, IngestLog WAL write, CRC32 computation, SQLite projection append.
- **Raw Evidence:** [`raw_evidence/benchmark_full_production_path.log`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/raw_evidence/benchmark_full_production_path.log)

| Configuration | Throughput | Latency p50 | Latency p95 | Latency p99 | Latency p99.9 |
|---|---|---|---|---|---|
| `fsync_policy="never"` | **2,509.4 EPS** | **248.10 µs** | **677.10 µs** | **3.38 ms** | **14.15 ms** |
| `fsync_policy="grouped_by_time"` | **1,957.5 EPS** | **367.40 µs** | **599.90 µs** | **4.16 ms** | **14.38 ms** |
