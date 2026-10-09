# MDRAP Phase 2 — Runtime Performance & Load Validation Report

**Document Identifier**: `MDRAP-PERF-P2-001`  
**Status**: VERIFIED & REPRODUCIBLE  
**Author**: Principal Systems Engineer  
**Harness**: `benchmarks/phase2_benchmark.py`  
**Raw Results**: `audit/phase2/benchmark_results.json`  
**Platform**: Windows 11 AMD64 / Python 3.13.1  

---

## 1. Executive Summary

Phase 2 operational hardening was subjected to rigorous micro- and macro-benchmarking to ensure that lifecycle management, bounded queues, backpressure drops, and supervision introduce zero regression to the core engine.

Key Highlights:
1. **Engine Canonical Step**: Achieved **10,897.9 EPS** with a median (p50) latency of **79.3 µs** and p99 latency of **266.0 µs**.
2. **Bounded Queue Saturation**: Non-blocking enqueuing and drop accounting executed at **530,168.3 ops/second** with p50 latency of **1.4 µs** and p99 latency of **3.8 µs**.
3. **Durable Lifecycle & Drain**: Cold engine startup completed in **74.35 ms**; graceful shutdown and WAL drain completed in **10.56 ms** with `drain_success = True`.

---

## 2. Benchmark Profiles & Measurements

### 2.1 Engine Canonical Step Latency Distribution (20,000 Events)

| Metric | Measured Value | Invariant Target | Status |
|---|---|---|---|
| **Throughput** | **10,897.9 EPS** | > 5,000 EPS | **PASS** |
| **Minimum Latency** | **49.9 µs** | — | — |
| **p50 (Median)** | **79.3 µs** | < 150 µs | **PASS** |
| **p90** | **113.5 µs** | < 250 µs | **PASS** |
| **p95** | **162.9 µs** | < 350 µs | **PASS** |
| **p99** | **266.0 µs** | < 500 µs | **PASS** |
| **p99.9** | **468.9 µs** | < 1,000 µs | **PASS** |
| **Maximum Tail Latency** | **1,803.6 µs** | < 5,000 µs | **PASS** |
| **Mean Latency** | **90.9 µs** | — | — |

### 2.2 Saturated Bounded Queue Profile (15,000 Operations, Queue Capacity 1,000)

| Metric | Measured Value | Description |
|---|---|---|
| **Throughput** | **530,168.3 Ops/sec** | Sustained push operations |
| **Enqueued** | **1,000 messages** | Filled queue to exact capacity |
| **Dropped** | **14,000 messages** | Accurately recorded dropped ticks |
| **p50 Latency** | **1.4 µs** | Time to test and increment drop counter |
| **p95 Latency** | **2.7 µs** | Near-zero overhead under severe congestion |
| **p99 Latency** | **3.8 µs** | Tail backpressure latency |

### 2.3 Production Runtime Lifecycle & Durability Drain (1,000 Events, Synchronous Fsync)

| Lifecycle Stage | Measured Duration | Notes |
|---|---|---|
| **Cold Initialization (`initialize()` + `start()`)** | **74.35 ms** | Opened WAL directory, created SQLite DB, restored projections |
| **Durable Ingest Throughput** | **261.6 EPS** | Includes synchronous physical disk fsync on every event |
| **Graceful Shutdown & Drain (`stop()`)** | **10.56 ms** | Cleanly joined workers, flushed pending writes, closed descriptors |
| **Drain Integrity** | **`drain_success = True`** | Zero dropped inflight events during shutdown |
