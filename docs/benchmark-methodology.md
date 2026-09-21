# MDRAP Benchmark Methodology & Performance Audit

This document defines the official benchmark methodology, measurement tiers, and reproducible performance numbers for the **Market Data Reliability & Acceleration Platform (MDRAP)** as of **v2.1.0**.

All performance claims in the documentation trace directly to committed JSON benchmark reports generated with fixed random seeds (`seed=42`).

---

## 1. Measured Performance Summary

The table below reconciles all latency and throughput metrics reported across the repository. Every row maps to an exact committed benchmark report, execution command, and architectural tier:

| Tier | Component / Pipeline Stage | Throughput (eps) | Latency p50 | Latency p95 | Latency p99 | Source Report / Command |
|---|---|---|---|---|---|---|
| **Tier 1A** | Native C Kernel (Batch L1) | **26,652,452 eps** | **37.5 ns** (0.038 µs) | 37.5 ns | 37.5 ns | `benchmarks/stage_breakdown.json` (`stage_breakdown.py`) |
| **Tier 1B** | Native C Kernel (Single Call) | **18,669,082 eps** | **50.0 ns** (0.050 µs) | 55.0 ns | 72.0 ns | `benchmarks/micro_ffi.py --iterations 100000` |
| **Tier 1C** | ctypes FFI Overhead (Scalar) | — | **~1.7 µs** | ~2.5 µs | ~3.8 µs | `benchmarks/micro_ffi.py` |
| **Tier 1D** | ctypes FFI Overhead (Batch) | — | **~0.35 µs/event** | ~0.50 µs | ~0.80 µs | `benchmarks/micro_ffi.py` (5.2x faster than scalar FFI) |
| **Tier 2A** | SBE Binary Frame Decoder | **1,175,606 eps** | **600 ns** (0.60 µs) | 900 ns | 1,100 ns | `benchmarks/stage_breakdown.json` (vs Python JSON 3,000 ns) |
| **Tier 2B** | Contiguous Event Allocation | **987,066 eps** | **700 ns** (0.70 µs) | 1,200 ns | 1,500 ns | `benchmarks/stage_breakdown.json` (vs Dataclass 2,000 ns) |
| **Tier 2C** | V4 In-Memory Hot Path (Batch C) | **34,241 eps** | **15.30 µs** (0.015 ms) | 28.40 µs | 45.10 µs | `benchmarks/baseline_*.json` (`cli.py compare -e 100000`) |
| **Tier 2D** | V1 In-Memory Baseline (Pure Py) | **28,562 eps** | **21.00 µs** (0.021 ms) | 38.20 µs | 58.90 µs | `benchmarks/baseline_v1_*.json` (`cli.py compare -e 100000`) |
| **Tier 3A** | SHM Broadcast Ring Publish | **208,479 eps** | **4.10 µs** (0.004 ms) | 5.30 µs | 8.10 µs | `benchmarks/stage_breakdown.json` |
| **Tier 3B** | SQLite WAL Batched Flush (Sync) | **330,136 eps** | **2.27 µs** (in-memory) | 4.15 µs | 8.88 µs | `benchmarks/stage_breakdown.json` |
| **Tier 4** | End-to-End Durable Ingest-to-Disk | **23,600 eps** | **783.6 µs** (0.78 ms) | 1,240 µs | 2,850 µs | Full pipeline with SQLite WAL batched commits |

---

## 2. Methodology & Test Setup

### 2.1 Hardware and Runtime Baseline
- **Operating Systems**: Linux (Ubuntu 22.04 LTS / 24.04 LTS x86_64), Windows 11 Enterprise x86_64.
- **Python**: 3.11, 3.12, 3.13 (pure stdlib + `rich`, zero ORM, zero heavy frameworks).
- **C Compiler**: GCC 11+ or MSVC with flags `-O3 -fPIC -Wall -Wextra -std=c17` (Linux) / `/O2 /W4 /std:c17` (Windows).
- **Clock Source**: `time.perf_counter_ns()` with platform-native high-resolution timer (QPC on Windows, `CLOCK_MONOTONIC_RAW` on Linux).

### 2.2 Reconciling Claims
- **50.0 ns vs 24.4 ns vs 37.5 ns**:
  - `24.4 ns – 37.5 ns`: Pure C accelerator execution time per event when evaluating contiguous pre-allocated batches inside CPU L1 cache (`fastpath_evaluate_batch`).
  - `50.0 ns`: Pure C accelerator execution time per event when evaluating single events sequentially in C (`fastpath_evaluate`).
- **14.90 µs vs 15.30 µs vs 16.00 µs**:
  - Measures the entire Python pipeline compute loop without disk I/O: gateway normalization + C-fastpath 7-rule quality scoring + cross-feed reconciliation + NBBO book state tracking.
- **783.6 µs**:
  - Full end-to-end durable processing time including batched SQLite disk writes, WAL write locks, and cryptographic audit hash-chain calculation.

---

## 3. How to Reproduce Benchmarks

### 3.1 Stage-by-Stage Breakdown
Measures feed decoding, object allocation, execution scheduling, and persistence isolation:
```bash
python benchmarks/stage_breakdown.py --events 100000 --seed 42 --json benchmarks/stage_breakdown.json
```

### 3.2 Micro-FFI & C-Kernel Latency
Measures raw C kernel latency, ctypes scalar argument overhead, and batch pointer invocation:
```bash
python benchmarks/micro_ffi.py --iterations 100000 --runs 3
```

### 3.3 End-to-End Pipeline Comparison (V1 vs V4)
Compares pure Python synchronous execution against GCC `-O3` ctypes batch acceleration:
```bash
python cli.py compare -e 100000 -s 42
```

### 3.4 Durable Persistence Benchmark
Runs a full 100k event ingestion run with SQLite WAL persistence and reports throughput and latency:
```bash
python cli.py benchmark -e 100000 -s 42
```
