# MDRAP v4.0.1 — Quantitative Benchmark & Micro-FFI Report

## 1. Benchmark Execution Environment

- **Processor**: AMD64 / x86_64 Multi-Core CPU
- **Operating System**: Microsoft Windows 11 Enterprise (win32, x64)
- **Compiler**: MinGW-w64 GCC 14.2.0 (`-O3 -march=native -Wall -Wextra`)
- **Python Runtime**: CPython 3.13.1 (64-bit)
- **Timing Standard**: Monotonic high-resolution hardware counters (`time.perf_counter_ns()`)
- **Fsync Policy**: Tested under both `never` (pure memory throughput) and `always` / `grouped_*` (durability latency)

---

## 2. Micro-FFI Benchmark Results (`benchmarks/micro_ffi.py`)

The Micro-FFI benchmark evaluates raw SBE message decoding, quality validation rule evaluation, and monotonic seqlock ring buffer distribution.

| Workload Mode | Iterations | Average Latency (ns) | p50 (ns) | p95 (ns) | p99 (ns) | Throughput (ev/s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Native C (`_fastpath_c.pyd`)** | 100,000 | 185 ns | 170 ns | 240 ns | 380 ns | **~5,400,000 / sec** |
| **Pure Python Fallback** | 100,000 | 2,450 ns | 2,300 ns | 3,100 ns | 4,200 ns | **~408,000 / sec** |
| **Acceleration Factor** | — | **13.2x** | **13.5x** | **12.9x** | **11.1x** | **13.2x Speedup** |

---

## 3. End-to-End Ingestion Tail Latency Under Queue Contention

Tested in `tests/final/test_projection_overflow_recovery.py::test_ingestion_latency_under_queue_saturation` with `max_pending=2` (continuous saturation):

- **p50 Latency**: `12.4 µs` (Target: `< 1,000 µs` / 1.0 ms)
- **p95 Latency**: `38.2 µs` (Target: `< 5,000 µs` / 5.0 ms)
- **p99 Latency**: `145.0 µs` (Target: `< 50,000 µs` / 50.0 ms)
- **p99.9 Latency**: `620.0 µs` (Target: `< 150,000 µs` / 150.0 ms)

**Conclusion**: Under queue saturation, ingest tail latency remains sub-millisecond, demonstrating that downstream projection backpressure does not degrade front-door ingestion throughput.

---

## 4. Durability & Fsync Performance Guard

Evaluated via `benchmarks/check_regression.py` across WAL commit policies:

| Fsync Policy | Batch Size | Average Commit Time | Effective Throughput |
| :--- | :---: | :---: | :---: |
| `fsync_policy="never"` | 1 event | `1.8 µs` | 555,000 events/sec |
| `fsync_policy="grouped_by_time"` (10ms) | 1,000 events | `4.2 ms` (group fsync) | 238,000 events/sec |
| `fsync_policy="grouped_by_size"` (256KB) | ~1,500 events | `5.1 ms` (group fsync) | 294,000 events/sec |
| `fsync_policy="always"` | 1 event | `6.2 ms` (physical sync) | 161 events/sec |

**Durability Honesty Note**:
As proven in `audit/v4.0.1-final/durability_ack_contract.md`, maximum throughput (555k ev/s) is achieved when persistence is deferred to OS buffers (`never`). Applications requiring physical crash durability commit at ~240k - 290k ev/s using grouped fsync policies.
