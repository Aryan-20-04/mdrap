# MDRAP v5.0 Phase 0: Baseline Performance Certification

## 1. Baseline Identity & Environment Attestation

- **Platform**: Market Data Reliability & Acceleration Platform (MDRAP)
- **Baseline Git Branch**: `v5.0-research-baseline`
- **Baseline Git Commit**: `1919caa66580f4f0cb0c93f7c324c4314c4155cb`
- **Parent Commits**: `b88c3b3` $\leftarrow$ `3ab9566`
- **Baseline Package Version**: `4.0.1` (`src/mdrap/_version.py`)
- **Execution Date**: 2026-11-05
- **Certification Authority**: Principal Systems Engineer & Independent Release Auditor

### Host Hardware & Operating System Profile `[SOURCE-VERIFIED]`
- **Operating System**: Windows 11 Build 26200 (64-bit AMD64)
- **Processor**: Intel Core i7-1255U (Intel64 Family 6 Model 154 Stepping 4, GenuineIntel)
  - Topology: 10 physical cores / 12 logical processors
  - 2 Performance Cores (P-Cores, Golden Cove, 4 threads, up to 4.7 GHz, 12 MB L3 cache)
  - 8 Efficiency Cores (E-Cores, Gracemont, 8 threads, up to 3.5 GHz)
  - Hardware Vector Extensions: AVX-2, SSE4.2, FMA3
- **System Memory**: 15.72 GB RAM
- **Storage Subsystem**: NVMe SSD (Measured sync speed: 243.1 MB/s, fsync latency: 4.11 ms)
- **Runtimes & Toolchains**:
  - Python: 3.13.1 (MSC v.1942 64-bit AMD64)
  - C Compiler: GCC 14.2.0 (MSYS2 MinGW-w64) / MSVC v19.42
  - Native Fastpath Status: `COMPILED (Vectorized C, AVX-2)`

---

## 2. Authoritative Baseline Performance Measurements

All metrics reported herein were empirically gathered by executing the reproducible test harness:
`python audit/v5/reproduction_scripts/run_v5_baseline.py`

### 2.1 Micro-FFI Boundary & Kernel Execution `[BENCHMARK-MEASURED]`
Raw evidence log: `audit/v5/raw_evidence/benchmark_micro_ffi.log`

| Benchmark Target | Mechanism | Operations Tested | Total Time | Per-Event Latency | Implied Throughput |
|:---|:---|:---:|:---:|:---:|:---:|
| **Pure C Native Kernel** | Direct compiled C function | 1,000,000 | 0.0388 s | **$38.8\ \text{ns}$** | **25,740,688 EPS** |
| **Python CFFI / ctypes** | Struct-by-ref boundary transition | 100,000 | 0.0418 s | **$418.0\ \text{ns}$** | **2,392,344 EPS** |
| **Pure Python Reference** | Pure Python object loop | 100,000 | 0.5831 s | **$4,900.0\ \text{ns}$** | **171,492 EPS** |

**Empirical Finding**: Crossing the Python-to-C FFI boundary ($418.0\ \text{ns}$) costs **10.8x more** than running the native C quality kernel itself ($38.8\ \text{ns}$). In the existing hybrid architecture, the FFI boundary consumes **91.5%** of the per-event processing budget!

---

### 2.2 In-Memory Transactional Engine Step (`Engine.submit()`) `[BENCHMARK-MEASURED]`
Raw evidence log: `audit/v5/raw_evidence/benchmark_engine_inmemory.log`
Sample size: 30,000 events (deterministic seed=42)

```
===========================================================================
MDRAP IN-MEMORY ENGINE SCORECARD (v4.0.1 Baseline)
===========================================================================
Metric                    | Measured Value   | Baseline Standard | Delta
---------------------------------------------------------------------------
Sustainable Throughput    | 45,958.3 EPS     | 34,489.0 EPS      | +33.3%
Latency Median (p50)      | 18.10 us         | 18.80 us          |  -3.7%
Latency Tail (p95)        | 47.40 us         | 60.40 us          | -21.5%
Latency Max-Tail (p99)    | 82.70 us         | 107.90 us         | -23.4%
===========================================================================
```

---

### 2.3 Stage-by-Stage Latency Deconstruction (100,000 Events) `[BENCHMARK-MEASURED]`
Raw evidence log: `audit/v5/raw_evidence/benchmark_stage_breakdown.log`

| Stage | Pure Python Implementation | Native / SBE / C Implementation | Speedup Factor | Python Time Share |
|:---|:---:|:---:|:---:|:---:|
| **Stage 1: Feed Decode** | $0.5110\ \text{s}$ (195.7k EPS, p50: $3.7\ \mu\text{s}$) | $0.0880\ \text{s}$ (1.137M EPS, p50: $0.6\ \mu\text{s}$) | **5.81x** | **28.8%** |
| **Stage 2: Allocation** | $0.4749\ \text{s}$ (210.6k EPS, p50: $2.6\ \mu\text{s}$) | $0.1100\ \text{s}$ (908.7k EPS, p50: $0.8\ \mu\text{s}$) | **4.32x** | **26.7%** |
| **Stage 3: Quality Eval** | $0.5831\ \text{s}$ (171.5k EPS, p50: $4.9\ \mu\text{s}$) | $0.0039\ \text{s}$ (25.74M EPS, avg: $38.8\ \text{ns}$) | **150.10x** | **32.8%** |
| **Stage 4: I/O & Persistence** | $0.2075\ \text{s}$ (481.9k EPS, p50: $1.7\ \mu\text{s}$) | $0.2589\ \text{s}$ (386.3k EPS, p50: $2.2\ \mu\text{s}$) | **0.80x** | **11.7%** |
| **Total In-Memory Time** | **1.7765 seconds** | **0.4608 seconds** | **3.86x** | **100.0%** |

**Empirical Finding**: In pure Python, **88.3% of execution time** is consumed by JSON parsing (28.8%), dataclass allocation (26.7%), and Python interpreter quality evaluation (32.8%).

---

### 2.4 Full Production Pipeline (With SQLite WAL Disk Persistence) `[BENCHMARK-MEASURED]`
Raw evidence log: `audit/v5/raw_evidence/benchmark_full_pipeline.log`

| Durability Policy | Throughput (EPS) | Latency p50 | Latency p95 | Latency p99 | Latency p99.9 |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Policy `never`** (OS Page Cache) | **2,767.8 EPS** | $190.6\ \mu\text{s}$ | $643.3\ \mu\text{s}$ | $3,523.2\ \mu\text{s}$ | $14,316.0\ \mu\text{s}$ |
| **Policy `grouped_by_time`** (Group Fsync) | **3,856.2 EPS** | $147.3\ \mu\text{s}$ | $383.2\ \mu\text{s}$ | $3,438.9\ \mu\text{s}$ | $11,728.1\ \mu\text{s}$ |

**Empirical Finding**: Ingesting directly into SQLite creates a catastrophic bottleneck that throttles the pipeline from 46,000 EPS down to **2,767 EPS**, with p99 tail latency inflating from $82.7\ \mu\text{s}$ to **$3.52\ \text{ms}$**.

---

## 3. Certification Sign-Off & Phase 0 Deliverable Acceptance

The baseline measurements recorded above establish the definitive empirical foundation against which all MDRAP v5.0 performance optimizations will be tested and verified.

- **Baseline Status**: CERTIFIED ACCURATE & REPRODUCIBLE.
- **Architectural Deliverables Complete**:
  - `docs/v5/research/` (8 exhaustive domain research papers).
  - `docs/v5/architecture/` (12 formal systems architecture specifications).
  - `audit/v5/raw_evidence/` (Full raw benchmark logs & hardware diagnostics).
  - `audit/v5/reproduction_scripts/` (Automated reproduction suite).
- **Production Code Status**: `src/` is strictly unmodified from commit `1919caa`.
- **Engineering Verdict**: Ready for Phase 0 formal review.
