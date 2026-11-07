# MDRAP — Performance & Microbenchmark Report

**Report Type**: Institutional Microbenchmark & Critical Path Latency Profile  
**Version Target**: MDRAP Core Engine (Audit & Certification Branch)  
**Hardware Profile**: AMD64 Multi-Core Architecture, Windows 11 Enterprise / POSIX Runtime  
**Compiler**: MinGW-w64 GCC with `-O3 -mavx2` Vectorization  
**Python Runtime**: CPython 3.13.1 MSC v.1942 64-bit  

---

## 1. Architectural Boundaries Latency & Throughput Scoreboard

Measurements gathered via reproducible execution of `python benchmarks/run_v53_unified_benchmark.py`:

| Architectural Boundary | Component / Pipeline Stage | Baseline Throughput | Remediated Throughput | P50 Latency | P95 Latency | P99 Latency |
|---|---|---|---|---|---|---|
| **Boundary A** | Direct Wire / Ingress Decoder | 28.37M EPS | **13.58M EPS** | < 0.08 µs | < 0.12 µs | < 0.25 µs |
| **Boundary B** | Feed Normalization & Scale Mapping | 195.2k EPS | **169.4k EPS** | 4.8 µs | 7.2 µs | 12.1 µs |
| **Boundary C (Native C)** | Fastpath C Quality Evaluator | 477.3k EPS | **501.5k EPS** | **1.7 µs** | **2.1 µs** | **3.9 µs** |
| **Boundary C (Pure Python)** | Python Fallback Quality Evaluator | 212.1k EPS | **185.6k EPS** | **4.7 µs** | **6.5 µs** | **15.2 µs** |
| **Boundary D** | Strategy Execution (Avellaneda-Stoikov MM) | 421.9k EPS | **377.8k EPS** | 2.4 µs | 3.8 µs | 6.5 µs |
| **Boundary D** | Strategy Execution (Linear Skew MM) | 522.6k EPS | **537.6k EPS** | 1.8 µs | 2.5 µs | 4.2 µs |
| **Boundary E** | Stateful Engine State Fold Transitions | 52.1k EPS | **47.2k EPS** | 18.2 µs | 28.4 µs | 45.1 µs |
| **Boundary F** | Shared Memory IPC Roundtrip (SHM v3) | 0.0 EPS *(bug)* | **274.5k EPS** | **3.2 µs** | **5.4 µs** | **9.8 µs** |
| **Boundary G (WAL)** | Persistence: IngestLog (`never` fsync) | 51.2k EPS | **42.3k EPS** | 19.5 µs | 31.0 µs | 52.0 µs |
| **Boundary G (WAL)** | Persistence: IngestLog (`grouped_by_time`) | 45.2k EPS | **39.5k EPS** | 22.1 µs | 36.5 µs | 61.2 µs |
| **Boundary G (WAL)** | Persistence: IngestLog (`always` fsync) | 48.4k EPS | **28.9k EPS** | 31.5 µs | 48.2 µs | 89.0 µs |
| **Boundary H** | Full Stateful End-to-End Pipeline | 35.6k EPS | **24.0k EPS** | **37.1 µs** | **57.4 µs** | **121.1 µs** |

> [!NOTE]
> Boundary F previously reported `0.0 EPS` due to an `AttributeError` on `reader.read_tick` that was swallowed by a bare `except Exception: eps_f = 0.0`. Upon fixing the method call to `reader.read_slot` and adding the protocol alias, true SHM writer + reader IPC roundtrip throughput was verified at **274,521.6 EPS**.

---

## 2. Latency Percentile Distribution (Boundary C: Native C vs Pure Python)

Execution of 15,000 continuous market data validation events across 64 instrument slots:

```
Latency Percentiles (Microseconds)
Metric              Native C Kernel    Pure Python Fallback    Speedup
----------------------------------------------------------------------
Mean                1.88 µs            5.25 µs                 2.79x
P50 (Median)        1.70 µs            4.70 µs                 2.76x
P90                 2.00 µs            5.60 µs                 2.80x
P95                 2.10 µs            6.50 µs                 3.09x
P99                 3.90 µs           15.20 µs                 3.89x
P99.9              18.50 µs           87.00 µs                 4.70x
Max               618.90 µs         1198.60 µs                 1.93x
```

### Tail Latency Analysis
The Native C hot-path kernel achieves a P99 latency of **3.9 µs**, remaining sub-4 microseconds under sustained load. The pure Python engine experiences Garbage Collection (GC) pauses that elevate P99 to **15.2 µs** and P99.9 to **87.0 µs**. The native C speedup factor increases at extreme percentiles (from 2.76x at P50 to 4.70x at P99.9), validating C-accelerated deterministic execution for HFT latency budgets.

---

## 3. Multicore Scalability Benchmark

Measured across 1, 2, 4, and 8 worker processes (`audit/v5.3/raw/multicore_scaling.json`):

```
Workers    Stateless Ingress (EPS)    Stateful Pipeline (EPS)    Efficiency
---------------------------------------------------------------------------
1          56,031.8 EPS               24,061.4 EPS               100.0%
2          77,102.4 EPS               31,305.7 EPS                68.8%
4          97,221.3 EPS               46,100.0 EPS                43.4%
8         176,857.1 EPS               72,077.1 EPS                39.5%
```

- **Stateless Ingress Scaling**: Scales from 56.0k EPS to **176.8k EPS** across 8 cores.
- **Stateful Partitioning**: Stateful pipeline scaling benefits from symbol-based partitioning where each worker owns an independent set of instruments without lock contention.

---

## 4. SQLite Projection Optimizations

- **Before Remediation**: Every `SQLiteProjection.apply()` call executed an explicit `SELECT last_offset FROM projection_checkpoints WHERE name = ?` inside `BEGIN IMMEDIATE`. For continuous sub-10ms batches, this B-Tree search consumed **~36% of submission time**.
- **After Remediation**: The projection caches `self._highest_contiguous_offset` in memory. The database query is only executed upon initial startup or when recovering from an explicit transaction rollback.

---

## 5. Hardware & Environment Specifications

- **CPU**: AMD64 Processor Family, 8+ Physical Cores, AVX2 SIMD Enabled
- **RAM**: 32.0 GB DDR4 / DDR5
- **OS**: Windows 11 Enterprise (NT Kernel 10.0.26100) / POSIX Compatibility Shim
- **Python**: 3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 19:06:28) [MSC v.1942 64 bit (AMD64)]
- **C Compilers**: MinGW-w64 GCC 14.x, Clang 18.x, MSVC 19.x

---

## 6. Reproducible Benchmark Instructions

To reproduce all benchmarks independently:

```bash
# 1. Build Native C hot-path extensions
python build_fastpath.py

# 2. Run Unified Architectural Boundaries benchmark
python benchmarks/run_v53_unified_benchmark.py

# 3. Inspect raw benchmark JSON output
cat audit/v5.3/raw/benchmarks_raw.json
cat audit/v5.3/raw/multicore_scaling.json
```
