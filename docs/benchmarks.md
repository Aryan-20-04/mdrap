# MDRAP Performance & Benchmark Methodology

This document outlines the performance tiers, benchmark methodology, hardware environments, and exact reproduction commands for all figures cited across the MDRAP platform.

---

## 1. Architectural Tiers & Why Numbers Differ

A frequent misunderstanding in high-throughput market data systems is comparing in-memory compute kernels against durable disk persistence. MDRAP operates across four distinct operational boundaries:

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. Native C Kernel (fastpath.c): 22M–30M EPS / 32–45 ns     │  Pure in-memory tick check
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Shared Memory IPC Fanout: 1.5M EPS / 0.4 µs handoff      │  Zero-copy cross-process ring
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Python End-to-End Pipeline: 120k EPS / 8.3 µs p50        │  Normalize + 15 rules + dedup
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Durable Storage (SQLite WAL): 21k–30k EPS / 4.6–6.8 ms   │  Non-volatile disk fsync
└─────────────────────────────────────────────────────────────┘
```

1. **Native C Kernel (`fastpath.c`)**: Evaluates structural rules, fixed-point integer scaling, and Welford variance in compiled C with AVX2 SIMD vectorization. With zero Python object allocation overhead, execution takes **$32\text{–}45\text{ ns}$ per tick** ($22\text{M}–30\text{M EPS}$).
2. **In-Memory IPC Fanout (`shm.py`, `async_fanout.py`)**: Distributes frames across local processes via seqlock shared memory ring buffers. Publisher handoff takes **$0.4\ \mu\text{s}$ p50** ($1.5\text{M EPS}$).
3. **End-to-End Python Pipeline (`engine.py`)**: Full normalization, Python dataclass allocation, 15 quality checks, and deduplication executed in the CPython runtime. Operates at **$8.3\ \mu\text{s}$ p50** ($120\text{k EPS}$).
4. **Durable Persistence to Disk (`ingestlog.py`, SQLite WAL)**: Commits events to disk with physical `fsync` barriers to survive host power loss. Throughput is constrained by physical storage write latency, yielding **$21.6\text{k}–29.6\text{k EPS}$** with batch latencies of $4.6\text{–}6.8\text{ ms}$.

---

## 2. Benchmark Hardware Baseline

All committed results trace directly to runs recorded in `benchmarks/results/honest_benchmarks.json` and `benchmarks/stage_breakdown.json`:

| Parameter | Baseline Environment Specification |
| :--- | :--- |
| **CPU** | Intel Core i7-12700H (12 physical cores, 20 logical threads @ 2.30 GHz base / 4.70 GHz boost) |
| **RAM** | 16 GB DDR5 |
| **Storage** | PCIe Gen4 NVMe SSD |
| **OS** | Windows 11 Enterprise / Ubuntu 22.04 LTS dual verification |
| **Python** | CPython 3.13.1 |
| **C Compiler** | GCC 13.2.0 (`-O3 -mavx2 -flto`) |

---

## 3. Comprehensive Results Table

| Stage / Component | Scope & Conditions | p50 Latency | p95 Latency | p99 Latency | Throughput (EPS) | Source JSON |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Native C Kernel** | Vectorized tick validation (`fastpath.c`) | 45.0 ns | 45.0 ns | 45.0 ns | **22,228,643** | `stage_breakdown.json` |
| **Binary SBE Decode** | Binary framing parsing vs JSON | 600 ns | 1.0 µs | 1.1 µs | **1,156,854** | `stage_breakdown.json` |
| **IPC Fanout (1 Client)** | Shared memory handoff | 0.4 µs | 0.5 µs | 0.6 µs | **1,379,976** | `honest_benchmarks.json` |
| **IPC Fanout (10 Clients)**| Shared memory handoff | 0.4 µs | 0.5 µs | 0.6 µs | **1,529,870** | `honest_benchmarks.json` |
| **IPC Fanout (25 Clients)**| Shared memory handoff | 0.9 µs | 1.2 µs | 1.5 µs | **655,974** | `honest_benchmarks.json` |
| **Python Engine Step** | In-memory normalization + 15 rules | 8.3 µs | 9.7 µs | 14.3 µs | **120,280** | `honest_benchmarks.json` |
| **SQLite WAL (Grouped)** | `fsync="grouped_by_time"`, batch 200 | 6.82 ms | 6.91 ms | 6.91 ms | **29,654** | `honest_benchmarks.json` |
| **SQLite WAL (Always)** | `fsync="always"`, batch 100 | 4.60 ms | 4.80 ms | 4.84 ms | **21,615** | `honest_benchmarks.json` |

---

## 4. Reproducing the Benchmarks

All benchmarks are automated and can be executed via standard scripts:

### End-to-End Pipeline & Storage Benchmarks
```bash
python benchmarks/run_honest_benchmarks.py
```
This script runs:
- 30 repetitions of 300 events (9,000 total samples) measuring in-process `engine.step()` latency percentiles.
- 1,800 durable events with `fsync="grouped_by_time"`.
- 900 durable events with `fsync="always"`.
- Multi-client publisher handoff and fanout throughput across 1, 10, and 25 subscribers.

### Native C Stage Breakdown
```bash
# Build native C accelerator first
python build_fastpath.py

# Run comparative breakdown
python benchmarks/bench_scale.py
```
Outputs execution latency across feed decode, memory allocation, rule scheduling, and I/O persistence.
