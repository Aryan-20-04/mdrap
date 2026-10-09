# MDRAP Phase 3 — Performance & Benchmark Report

**Document Identifier**: `MDRAP-PERF-P3-001`  
**Date**: October 9, 2026  
**Host Architecture**: Microsoft Windows 11 Enterprise (amd64), Python 3.13.1  
**Benchmark Suite**: `benchmarks/phase3_benchmark.py`  
**Raw Results Output**: `audit/phase3/benchmark_results.json`  

---

## 1. Measured Performance Results

| Workload Scenario | Total Operations | Elapsed Time | Throughput | Latency p50 | Latency p95 | Latency p99 | Max Latency |
|---|---|---|---|---|---|---|---|
| **Ingress Replay Adapter** | 50,000 events | 0.2689 s | **185,969.1 EPS** | 4.6 µs | 6.1 µs | 11.5 µs | 503.9 µs |
| **Durable Usage Metering** | 20,000 records | 3.3253 s | **6,014.4 ops/s** | 65.8 µs | 174.7 µs | 3,022.8 µs | 26.75 ms |
| **HA Heartbeat State Machine** | 50,000 heartbeats | 0.0971 s | **514,908.7 ops/s** | 1.7 µs | 1.9 µs | 2.4 µs | 88.1 µs |

---

## 2. Analysis & Architectural Observations

1. **Ingress Polling & Normalization**: The normalized `ReplayFeedAdapter` processes and normalizes market data at over 185,000 events per second in pure Python with sub-5µs median latency.
2. **Durable Compliance Metering**: Each metered batch performs a synchronous SQLite WAL transaction with unique idempotency indexing, achieving >6,000 ops/s. For workloads generating millions of ticks, grouping ticks into client batches (e.g. 100 ticks per batch) allows metering up to 600,000 ticks/sec with guaranteed crash durability.
3. **High Availability Heartbeat Engine**: At over 514,000 operations per second with sub-2µs latency, failover state tracking and epoch fencing introduce virtually zero CPU overhead to the cluster runtime.
