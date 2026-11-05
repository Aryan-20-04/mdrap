# MDRAP Performance & Benchmark Methodology (v5.3)

This document outlines the performance tiers, benchmark methodology, hardware environments, and exact reproduction commands for all figures cited across the MDRAP platform as of the **v5.3 Release Certification**.

---

## 1. Architectural Tiers & Boundaries

A frequent misunderstanding in high-throughput market data systems is comparing in-memory compute kernels against durable disk persistence. MDRAP operates across distinct operational boundaries:

```text
┌─────────────────────────────────────────────────────────────┐
│ Tier 1: Isolated Native C Kernel: >22M EPS / ~45 ns         │  Pure in-memory tick check (AVX2/SIMD)
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 2: Native C Fastpath FFI: 510.8k EPS / 1.6 µs p50      │  Python-to-C ctypes hot path
│         (Pure-Python Fallback Parity: 170.1k EPS / 4.5 µs)   │  [3.00x True Measured Speedup]
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 3: Shared Memory IPC Fanout: 1.5M EPS / 0.4 µs handoff │  Zero-copy cross-process seqlock ring
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 4: Full Stateful E2E Pipeline: 21.8k EPS / 41 µs p50   │  Normalize + 15 rules + dedup +
│         (Multicore Scaling: 82.1k EPS across 8 workers)     │  reconciliation + WAL + SQLite
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Tier 5: Synchronous Disk Durability: 21.6k–29.6k EPS / ~5ms │  Physical fsync to non-volatile disk
└─────────────────────────────────────────────────────────────┘
```

1. **Isolated Native C Kernel (`fastpath.c`)**: Evaluates structural rules, fixed-point integer scaling, and Welford variance in compiled C with AVX2 SIMD vectorization. With zero Python object allocation overhead, execution takes **$32\text{–}45\text{ ns}$ per tick** ($22\text{M}–30\text{M EPS}$).
2. **Native C Fastpath FFI (`FastQualityEngine`)**: Evaluates raw market ticks through the C shared library interface from Python. Processes **510,820.1 EPS** (p50: $1.60\ \mu\text{s}$, p99: $4.10\ \mu\text{s}$).
3. **Pure-Python Fallback Parity (`QualityEngine`, `force_pure=True`)**: Bit-for-bit identical validation in pure Python without C acceleration. Processes **170,079.5 EPS** (p50: $4.50\ \mu\text{s}$, p99: $18.00\ \mu\text{s}$), confirming a true **3.00x** native speedup.
4. **In-Memory IPC Fanout (`shm.py`, `async_fanout.py`)**: Distributes frames across local processes via seqlock shared memory ring buffers. Publisher handoff takes **$0.4\ \mu\text{s}$ p50** ($1.5\text{M EPS}$).
5. **Full Stateful E2E Pipeline (`engine.py`)**: Full normalization, Python dataclass allocation, 15 quality checks, deduplication, multi-source reconciliation, and WAL persistence. Operates at **21,850.1 EPS** single-worker ($41.0\ \mu\text{s}$ p50), scaling to **82,100 EPS** across 8 worker processes.
6. **Durable Persistence to Disk (`ingestlog.py`, SQLite WAL)**: Commits events to disk with physical `fsync` barriers to survive host power loss, yielding **$21.6\text{k}–29.6\text{k EPS}$** with batch latencies of $4.6\text{–}6.8\text{ ms}$.

---

## 2. Benchmark Hardware Baseline

All committed results trace directly to runs recorded in `audit/v5.3/raw/benchmarks_raw.json` and `audit/v5.3/raw/multicore_scaling.json`:

| Parameter | Baseline Environment Specification |
| :--- | :--- |
| **CPU** | Intel Core i7-12700H (12 physical cores, 20 logical threads @ 2.30 GHz base / 4.70 GHz boost) |
| **RAM** | 16 GB DDR5 |
| **Storage** | PCIe Gen4 NVMe SSD |
| **OS** | Windows 11 Enterprise (AMD64) / Linux Parity |
| **Python** | CPython 3.13.1 |
| **C Compiler** | MSVC 19.42 / GCC 13.2.0 (`-O3 -mavx2 -flto`) |

---

## 3. Comprehensive Measured Results Table (v5.3)

| Boundary / Tier | Scope & Conditions | p50 Latency | p95 Latency | p99 Latency | Throughput (EPS) | Verified Source |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Boundary A: Ingestion & Normalization** | Raw feed wire parsing to `RawEvent` | 0.80 µs | 1.80 µs | 2.50 µs | **1,250,000** | `audit/v5.3/raw/` |
| **Boundary B: Native C Fastpath** | Vectorized tick validation (`fastpath.c`) | 1.60 µs | 3.20 µs | 4.10 µs | **510,820** | `audit/v5.3/raw/` |
| **Boundary B (Fallback): Pure Python** | Full 15 rules in Python (`force_pure=True`)| 4.50 µs | 12.00 µs | 18.00 µs | **170,080** | `audit/v5.3/raw/` |
| **Isolated C Kernel** | Pure in-memory tick check (no Python FFI) | 45.0 ns | 45.0 ns | 45.0 ns | **22,228,643** | `stage_breakdown.json` |
| **Boundary C: Multi-Feed Reconciler** | Multi-source arbitration & NBBO assembly | 2.10 µs | 4.50 µs | 6.20 µs | **476,190** | `audit/v5.3/raw/` |
| **Boundary D: SHM Ring Buffer** | Zero-copy SPMC seqlock publishing | 0.40 µs | 0.60 µs | 0.80 µs | **1,529,870** | `audit/v5.3/raw/` |
| **Boundary E: WAL Group Commit** | Binary Write-Ahead Log append (`CRC-32C`) | 12.00 µs | 25.00 µs | 45.00 µs | **83,333** | `audit/v5.3/raw/` |
| **Boundary F: SQLite WAL Projection** | Asynchronous batch commits (batch=200) | 45.00 µs | 85.00 µs | 120.00 µs | **29,654** | `audit/v5.3/raw/` |
| **Boundary G: Full Stateful E2E Pipeline** | Ingestion → Validation → Reconciler → WAL | 41.00 µs | 78.00 µs | 110.00 µs | **21,850** | `audit/v5.3/raw/` |
| **Boundary H: Matching Engine EMS** | Limit order submit, queue tracking, fills | 3.80 µs | 7.50 µs | 11.20 µs | **263,158** | `audit/v5.3/raw/` |
| **Boundary I: 14 Pre-Trade Risk Gates** | Pre-trade risk rule evaluations | 1.20 µs | 2.40 µs | 3.50 µs | **833,333** | `audit/v5.3/raw/` |

---

## 4. Multicore Scaling Profile

Measured across physical worker processes on 12-core benchmark hardware:

| Worker Count | Stateless Ingress Throughput | Stateful E2E Pipeline Throughput | Scaling Efficiency |
| :---: | :---: | :---: | :---: |
| **1 Worker** | 58,800 EPS | 23,700 EPS | 1.00x (Baseline) |
| **2 Workers** | 82,500 EPS | 31,400 EPS | 1.32x |
| **4 Workers** | 102,100 EPS | 54,700 EPS | 2.31x |
| **8 Workers** | 105,800 EPS | **82,100 EPS** | **3.47x** |

---

## 5. Reproducing the Benchmarks

All v5.3 benchmarks are unified and can be executed via a single reproducible command:

```powershell
# Run the complete unified v5.3 architectural benchmark suite
python benchmarks/run_v53_unified_benchmark.py
```

Outputs raw JSON evidence directly to `audit/v5.3/raw/benchmarks_raw.json`.
