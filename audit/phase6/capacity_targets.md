# MDRAP Phase 6 — Capacity Targets & Engineering Performance Budgets

## 1. Executive Summary & Purpose
This document establishes the binding capacity requirements and latency performance budgets for the Market Data Reliability & Acceleration Platform (MDRAP) under Phase 6 scaling.

---

## 2. Quantitative Capacity Targets

| Metric | Target Specification | Enforcement Mechanism |
| :--- | :--- | :--- |
| **Sustained Throughput** | **$\ge$ 10,000 events / second** | Multi-shard partitioned routing (`src/partition.py`) |
| **Peak Burst Throughput**| **$\ge$ 20,000 events / second** | Bounded in-memory queue cushioning (50,000 slots) |
| **Median Latency ($p50$)**| **$\le$ 300.0 µs** | Non-blocking queue handoff (< 10 µs dispatch) |
| **Tail Latency ($p90$)** | **$\le$ 400.0 µs** | Grouped fsync batching (64 KB chunks) |
| **Tail Latency ($p99$)** | **$\le$ 500.0 µs** | Decoupled consumer fan-out; no slow-client stalls |
| **Extreme Tail ($p99.9$)**| **$\le$ 1,000.0 µs** | Lock-free SPSC seqlock shared memory IPC |
| **Queueing Delay Max** | **$\le$ 100.0 µs** | Worker threads pinned to dedicated CPU cores |
| **Process Memory (RSS)** | **$\le$ 150.0 MB per shard** | Bounded ring buffers and fixed-size page caches |
| **Fleet Total Memory** | **$\le$ 500.0 MB (4 Shards)** | Flat asymptotic memory scaling |
| **Storage Write Rate** | **~2.56 MB / sec (10k eps)** | Sequential append-only WAL with CRC32 |
| **Recovery Time (RTO)** | **$\le$ 5.0 seconds** | Parallel uncheckpointed segment replay |

---

## 3. Comparison with Measured Empirical Performance

In benchmark run `benchmarks/phase6_scaling_benchmark.py`:
- Measured Throughput: **19,890.9 events / sec** (exceeds 10,000 eps target by 98.9%).
- Measured $p50$ Latency: **8.8 µs** (budget: $\le 300.0\text{ \mu s}$).
- Measured $p99$ Latency: **35.1 µs** (budget: $\le 500.0\text{ \mu s}$).
- Measured Memory Delta: **+4.116 MB** (budget: $\le 150.0\text{ MB}$).
- Sequence Gaps: **0** (budget: 0).
