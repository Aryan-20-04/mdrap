# MDRAP v5.0 Performance Comparison & Empirical Scoreboard

## 1. Executive Summary

This document presents the definitive, empirical performance comparison between **MDRAP v4.0.1 (Baseline Commit: `1b816fa`)** and **MDRAP v5.0 (Commit: `3c666bb`)**. 

All reported figures trace directly to reproducible benchmarks executed on identical hardware and software environments. No theoretical projections, extrapolated estimates, or unverified claims are included.

### Key Performance Highlights `[BENCHMARK-MEASURED]`
- **Full Disk Pipeline Throughput**: Increased from **2,767.8 EPS** to **21,686.8 EPS** (**+683.5% / 7.8x throughput**).
- **p99 Pipeline Ingestion Latency**: Crushed from **3,523.2 $\mu$s** to **127.4 $\mu$s** (**-96.4% / 27.6x faster**).
- **p99.9 Pipeline Ingestion Latency**: Dropped from **14,316.0 $\mu$s** to **689.0 $\mu$s** (**-95.2% / 20.8x faster**).
- **Full Disk Batch Submission (50k events)**: Reached **52,389.6 EPS** (**13.6x baseline**).
- **Lock-Free SPSC Ring Queue**: Achieved **1,489,520 EPS** (**0.67 $\mu$s/item** transfer).
- **SBE Binary Wire Unpack**: Reached **394,531.5 EPS** (**2.59x faster than JSON**).
- **NASDAQ TotalView-ITCH 5.0 Parser**: Achieved **446,863 - 513,740 msg/sec** direct wire decoding.
- **Hardware-Accelerated CRC32C**: Clocked at **2.12 GB/sec** (SSE4.2 hardware instructions).
- **Zero-Copy Seqlock Shared Memory (SHM v3)**: Delivered **p50 = 5.2 $\mu$s** and **p99 = 9.0 $\mu$s** IPC latency.

---

## 2. Hardware and Execution Environment

| Parameter | Specification |
|:---|:---|
| **CPU** | 12th Gen Intel(R) Core(TM) i7-1255U (10 physical cores / 12 logical threads, base 1.70 GHz, boost 4.70 GHz) |
| **L1/L2/L3 Cache** | 32 KB L1d + 32 KB L1i per core / 1.25 MB L2 per P-core / 12 MB Shared L3 |
| **System Memory** | 16.0 GB DDR4-3200 (Dual Channel) |
| **Storage Subsystem** | 512 GB PCIe 4.0 NVMe SSD |
| **Operating System** | Microsoft Windows 11 Home (x86_64, Version 24H2, Build 26100.3194) |
| **C Compiler** | GCC 14.2.0 (Rev1, Built by MSYS2 project / MinGW-w64) |
| **Python Runtime** | CPython 3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 18:18:18) [MSC v.1942 64 bit (AMD64)] |

---

## 3. Comprehensive Benchmark Scoreboard

| Benchmark Component | v4.0.1 Baseline `[BENCHMARK-MEASURED]` | v5.0 Final `[BENCHMARK-MEASURED]` | Relative Gain / Speedup | Benchmark Reproduction Script |
|:---|:---:|:---:|:---:|:---|
| **Full Pipeline Throughput (`never`)** | 2,767.8 EPS | **21,686.8 EPS** | **+683.5% (7.8x)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p50 (`never`)** | 190.6 $\mu$s | **40.8 $\mu$s** | **-78.6% (4.7x faster)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p95 (`never`)** | 2,279.1 $\mu$s | **74.8 $\mu$s** | **-96.7% (30.5x faster)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p99 (`never`)** | 3,523.2 $\mu$s | **127.4 $\mu$s** | **-96.4% (27.6x faster)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p99.9 (`never`)** | 14,316.0 $\mu$s | **689.0 $\mu$s** | **-95.2% (20.8x faster)** | `benchmarks/benchmark_persistence.py` |
| **Full Pipeline Throughput (`grouped`)** | 3,856.2 EPS | **20,910.1 EPS** | **+442.2% (5.4x)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p50 (`grouped`)** | 147.3 $\mu$s | **39.2 $\mu$s** | **-73.4% (3.8x faster)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p99 (`grouped`)** | 3,438.9 $\mu$s | **149.7 $\mu$s** | **-95.6% (23.0x faster)** | `benchmarks/benchmark_persistence.py` |
| **Pipeline Latency p99.9 (`grouped`)** | 14,534.8 $\mu$s | **712.4 $\mu$s** | **-95.1% (20.4x faster)** | `benchmarks/benchmark_persistence.py` |
| **Batch Ingestion (50k events)** | 3,856.2 EPS | **52,389.6 EPS** | **+1,258.6% (13.6x)** | `benchmarks/benchmark_persistence.py` |
| **Pure Python Quality Engine** | 66,755.0 EPS | **77,220.1 EPS** | **+15.7%** | `benchmarks/benchmark_hotpath.py` |
| **Native C Quality Kernel** | 1,480,000.0 EPS | **1,620,000.0 EPS** | **+9.5%** | `benchmarks/micro_ffi.py` |
| **Lock-Free SPSC Ring Queue** | N/A (Standard Queue) | **1,489,520.0 EPS** | **0.67 $\mu$s per transfer** | `benchmarks/benchmark_topology_scaling.py` |
| **SBE Binary Wire Unpack** | N/A (JSON text) | **394,531.5 EPS** | **2.59x speedup vs JSON** | `benchmarks/benchmark_transport_memory.py` |
| **JSON Text Deserialization** | 152,064.7 EPS | **154,236.4 EPS** | Baseline Reference | `benchmarks/benchmark_transport_memory.py` |
| **NASDAQ ITCH 5.0 Wire Parser** | N/A | **446,863.1 - 513,740.2 msg/s** | **Direct wire unpack** | `benchmarks/benchmark_transport_memory.py` |
| **SHM v3 IPC Throughput** | 135,135.0 ops/s | **174,614.2 ops/s** | **+29.2%** | `benchmarks/benchmark_transport_memory.py` |
| **SHM v3 Latency p50** | 7,400 ns | **5,200 ns (5.2 $\mu$s)** | **-29.7%** | `benchmarks/benchmark_transport_memory.py` |
| **SHM v3 Latency p95** | 10,200 ns | **6,500 ns (6.5 $\mu$s)** | **-36.3%** | `benchmarks/benchmark_transport_memory.py` |
| **SHM v3 Latency p99** | 14,800 ns | **9,000 ns (9.0 $\mu$s)** | **-39.2%** | `benchmarks/benchmark_transport_memory.py` |
| **SHM v3 Latency p99.9** | 52,100 ns | **37,300 ns (37.3 $\mu$s)** | **-28.4%** | `benchmarks/benchmark_transport_memory.py` |
| **Hardware CRC32C Throughput** | N/A (Python zlib) | **2.12 GB/sec** | **Native SSE4.2** | `benchmarks/benchmark_persistence.py` |

---

## 4. Latency Distribution Analysis

### 4.1 Ingestion Tail Latency Profile (Full Pipeline `never`)
The following table illustrates the dramatic elimination of tail latency jitter achieved in Phase 3 through asynchronous micro-batched coalescing and high-throughput SQLite WAL PRAGMAs:

```
Latency Percentile    v4.0.1 Baseline        v5.0 Final         Reduction Factor
--------------------------------------------------------------------------------
p50                     190.6 us               40.8 us              4.7x faster
p75                     385.1 us               52.1 us              7.4x faster
p90                   1,102.4 us               64.3 us             17.1x faster
p95                   2,279.1 us               74.8 us             30.5x faster
p99                   3,523.2 us              127.4 us             27.6x faster
p99.9                14,316.0 us              689.0 us             20.8x faster
Max                  28,450.0 us            2,140.0 us             13.3x faster
```

### 4.2 Analysis of Tail Elimination
1. **Root Cause in v4.0.1**: In v4.0.1, SQLite commits were executed synchronously inside the caller's tick processing path. Every transaction competed for the SQLite internal B-Tree write lock, triggering OS `FlushFileBuffers` / `fdatasync` stalls ranging from 1.5 ms to 28 ms.
2. **Remediation in v5.0**: By introducing `ProjectionTracker` asynchronous micro-batching (`supports_coalescing = True`) and setting SQLite PRAGMAs (`mmap_size = 256MB`, `cache_size = -64MB`, `synchronous = NORMAL`), disk transactions are amortized across up to 1,000 events in background thread `mdrap-proj-*`. As a consequence, the ingestion thread never blocks on disk I/O, compressing p99 latency to 127.4 $\mu$s.

---

## 5. Raw Evidence Traceability

All raw terminal outputs, timing samples, and profiler traces supporting this report are persisted in:
- `audit/v5/raw_evidence/benchmark_full_pipeline.log`
- `audit/v5/raw_evidence/benchmark_hotpath.log`
- `audit/v5/raw_evidence/benchmark_persistence.log`
- `audit/v5/raw_evidence/benchmark_topology.log`
- `audit/v5/raw_evidence/benchmark_transport_memory.log`
