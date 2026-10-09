# MDRAP Phase 6 — Measurable Scaling Targets and Engineering Objectives

## 1. Executive Summary & Purpose
This document establishes measurable, enforceable engineering targets for MDRAP Phase 6. All proposed scaling architectures, partitioning strategies, and consumer fan-out mechanisms will be benchmarked against these specific quantitative criteria.

---

## 2. Quantitative Performance & Scaling Targets

| Performance Metric | Baseline (Phase 5 Single-Node) | Phase 6 Sharded Target (2 Partitions) | Phase 6 Fleet Target (4 Partitions) |
| :--- | :--- | :--- | :--- |
| **Sustained Throughput** | 3,166 events / sec | **$\ge$ 6,000 events / sec** | **$\ge$ 12,000 events / sec** |
| **Peak Burst Throughput**| 10,000 events / sec | **$\ge$ 20,000 events / sec** | **$\ge$ 40,000 events / sec** |
| **Median Latency ($p50$)**| 278.9 µs | **$\le$ 300.0 µs** | **$\le$ 300.0 µs** |
| **Tail Latency ($p99$)** | 412.3 µs | **$\le$ 500.0 µs** | **$\le$ 500.0 µs** |
| **Peak Latency ($\max$)** | 712.5 µs | **$\le$ 2,500.0 µs** | **$\le$ 5,000.0 µs** |
| **Active Symbols Supported**| 20 tickers | **250 tickers** | **1,000 tickers** |
| **Concurrent Consumers** | 2 consumers | **10 consumers** | **25+ consumers** |
| **Process Memory (RSS)** | 43.0 MB | **$\le$ 150.0 MB per shard** | **$\le$ 500.0 MB total fleet** |
| **Slow-Consumer Eviction**| Manual disconnect | **Automated $\le$ 50 ms** | **Automated $\le$ 50 ms** |
| **Shard Recovery Time** | ~7.5 seconds | **$\le$ 5.0 seconds** | **$\le$ 5.0 seconds** |

---

## 3. Scaling Efficiency Invariants

To be deemed successful, horizontal partitioning must demonstrate:

1. **Sub-Linear Latency Degradation**: As consumer count scales from 2 to 10, $p99$ tail latency must not increase by more than 25% (must remain $\le 500\text{ \mu s}$).
2. **Linear Shard Throughput Scaling**: Ingest capacity across $K$ independent shards must scale as:
   $$\text{Throughput}_{\text{fleet}} \ge 0.90 \times \sum_{i=1}^{K} \text{Throughput}_{i}$$
3. **Strict Memory Bounding**: Memory consumption must exhibit a flat asymptotic ceiling, with RSS delta $\le 5.0\text{ MB}$ over 100,000 continuous event iterations.
