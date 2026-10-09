# MDRAP Phase 7 — Benchmark Governance & Measurement Framework

## 1. Executive Summary & Purpose
In low-latency financial systems, performance claims without reproducible conditions, noise controls, and warm-up policies are meaningless.

This framework defines the formal **Benchmark Governance Policy** for MDRAP, governing test execution, warm-up criteria, noise isolation, metric aggregation, and comparative interpretation rules.

---

## 2. Standardized Benchmark Profiles

### Profile A: Sharded Ingestion & Fan-Out Benchmark
- **Target Harness**: [`benchmarks/phase6_scaling_benchmark.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/benchmarks/phase6_scaling_benchmark.py)
- **Workload**: 20,000 canonical events across 500 active symbols (`AAPL`, `MSFT`, `GOOG`, `NVDA`, `AMZN`, etc.).
- **Topology**: 2 Shards (Range mode: `A-L` $\rightarrow$ Shard 0, `M-Z` $\rightarrow$ Shard 1), 2 concurrent TCP fan-out consumers.
- **Warm-Up Policy**: 2,000 preliminary events to pre-warm CPU instruction caches, JIT structures, and OS buffer caches before measurement start.
- **Metrics Collected**:
  - Sustained throughput (events/sec)
  - Processing latency distribution: p50, p90, p95, p99, p99.9, and max
  - Resident Set Size (RSS) memory delta (MB)
  - Fan-out queue dwell time and drop count

### Profile B: Full Monolithic Pipeline Benchmark
- **Target Harness**: [`benchmarks/run_performance_suite.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/benchmarks/run_performance_suite.py)
- **Workload**: 50,000 events streamed through single-node gateway, quality evaluator, and SQLite batch drainer.
- **Metrics Collected**: End-to-end processing latency, batch drain efficiency, and CPU utilization.

---

## 3. Noise Control & Environmental Discipline
1. **Deterministic Seeding Invariant**: All synthetic feed streams must initialize with `seed=42`.
2. **Process Affinity & Core Isolation**: On Linux test runners, benchmarks execute with `taskset -c 2,3` to isolate benchmark threads from OS background interrupts.
3. **Power Plan / Frequency Scaling**: Benchmarks require high-performance governor (`performance` on Linux, High Performance on Windows) to prevent CPU frequency scaling artifacts.
4. **Outlier Filtering**: Reported percentiles ($p50, p95, p99$) must reflect raw observed latencies without artificial post-hoc smoothing.
