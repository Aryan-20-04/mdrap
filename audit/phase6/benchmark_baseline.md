# MDRAP Phase 6 — Authoritative Benchmark Baseline & Comparison Framework

## 1. Executive Summary & Purpose
This document records the official baseline performance measurements established at the conclusion of Phase 5 (`audit/phase5/benchmark_results.json`) prior to any architectural sharding or fan-out modifications. All Phase 6 performance evaluations are directly compared against these figures.

---

## 2. Baseline Benchmark Profile (Phase 5 Single-Node)

| Metric | Measured Baseline Value | Measurement Method |
| :--- | :--- | :--- |
| **Topology** | Single-Node Host (Profile A) | Windows 11 / Python 3.13.1 |
| **Workload Size** | **25,000 events** | Deterministic binary replay (`seed=42`) |
| **Active Symbols** | 20 equity tickers | AAPL, MSFT, NVDA, SPY, etc. |
| **Sustained Throughput**| **3,166.0 events / second** | End-to-end pipeline processing |
| **Median Latency ($p50$)**| **278.9 µs** | High-resolution tick timestamping |
| **Tail Latency ($p90$)** | **365.1 µs** | High-resolution tick timestamping |
| **Tail Latency ($p95$)** | **382.4 µs** | High-resolution tick timestamping |
| **Tail Latency ($p99$)** | **412.3 µs** | High-resolution tick timestamping |
| **Extreme Tail ($p99.9$)**| **489.1 µs** | High-resolution tick timestamping |
| **Peak Latency ($\max$)**| **712.5 µs** | Maximum observed tick latency |
| **Memory Allocation Delta**| **+0.881 MB** | `tracemalloc` resident memory delta |
| **Sequence Monotonicity**| **0 Gaps (100.0%)** | Downstream consumer gap audit |

---

## 3. Structural Limits of the Single-Node Baseline
- Single-process Python execution saturates at ~3,500 eps due to thread scheduling overhead.
- Single SQLite canonical database experiences write lock serialization during bursts.
- Phase 6 target: Multiply throughput through partitioned sharding while preserving sub-millisecond tail latencies.
