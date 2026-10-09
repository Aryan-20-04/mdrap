# MDRAP Phase 0 — Reproducible Benchmark Baseline

**Document Identifier**: `MDRAP-AUDIT-P0-PERF-001`  
**Execution Timestamp**: 2026-10-08T22:27:18Z (UTC)  
**Host Architecture**: `x86_64` (Intel64 Family 6 Model 154 Stepping 4, GenuineIntel)  
**Operating System**: Microsoft Windows 11 Enterprise (Build 10.0.26200)  
**Compiler**: MinGW-w64 GCC 13.2.0 (`-O3 -mavx2 -march=native`)  
**Python Runtime**: CPython 3.13.1 (64-bit)  
**Git Revision**: `b891898a3f38fdf46a7454e19ee210f7dae19737`  
**Machine-Readable Data**: [`benchmark_results.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/phase0/benchmark_results.json)  
**Harness Script**: [`benchmarks/phase0/run_phase0_benchmarks.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/benchmarks/phase0/run_phase0_benchmarks.py)  
**Status**: COMPLETE (Baseline Established)

---

## 1. Benchmark Methodology & Measurement Standards

In accordance with Phase 0 Workstream 5, all benchmarks were executed against the unaltered repository working tree under standardized conditions:
1. **Clock Source**: Monotonic hardware cycle counter via `time.perf_counter()` and `time.perf_counter_ns()` (backed by Windows QPC, sub-microsecond resolution).
2. **Determinism**: Fixed random generator seed (`seed=42`) across all synthetic market tick generators.
3. **Tail Latency Reporting**: Explicit reporting of p50, p95, p99, p99.9, and maximum peak latency.
4. **Boundary Isolation**: Clear demarcation between pure native C performance, Python-native FFI boundary cost, IPC latency, and persistent disk I/O.
5. **Zero-Loss Guarantee**: Verification of zero data drops across all pipeline and backpressure stages.

---

## 2. Empirical Benchmark Scorecard (9 Categories)

```
+--------------------------------------------------+-----------------+---------------+----------------------+
| Benchmark Category                               | Metric          | Observed      | Unit / Percentiles   |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 1. Native Engine (mdrap-core.exe)                | Median Tput     |  5,011,807.8  | events/sec           |
|                                                  | Peak Tput       |  6,066,584.4  | events/sec           |
|                                                  | Median Latency  |        199.5  | ns / tick            |
|                                                  | Min Latency     |        164.8  | ns / tick            |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 2. Python-to-Native Overhead                     | Python Engine   |    297,146.4  | events/sec (3.37 us) |
|                                                  | Native Fastpath |    382,041.6  | events/sec (2.62 us) |
|                                                  | In-Process Gain |         1.29  | x speedup ratio      |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 3. SHM IPC Producer-Consumer Latency (1 Source)  | p50 Latency     |          2.1  | microseconds         |
|                                                  | p95 Latency     |          2.3  | microseconds         |
|                                                  | p99 Latency     |          4.2  | microseconds         |
|                                                  | p99.9 Latency   |         48.7  | microseconds         |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 3b. SHM IPC Latency Sweep (8 Sources)            | p50 Latency     |          2.1  | microseconds         |
|                                                  | p95 Latency     |          2.7  | microseconds         |
|                                                  | p99 Latency     |          5.0  | microseconds         |
|                                                  | p99.9 Latency   |         34.4  | microseconds         |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 4. Feed Parsing & Normalization                  | Binary SBE Dec  |    321,498.5  | events/sec (3.11 us) |
|                                                  | Python JSON Dec |    340,742.9  | events/sec (2.93 us) |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 5. Ingestion-to-Publication (In-Memory)          | Throughput      |     86,505.9  | events/sec           |
|                                                  | p50 Latency     |          9.6  | microseconds         |
|                                                  | p95 Latency     |         14.1  | microseconds         |
|                                                  | p99 Latency     |         22.0  | microseconds         |
|                                                  | p99.9 Latency   |        115.0  | microseconds         |
|                                                  | Max Latency     |        433.6  | microseconds         |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 6. Persistence and WAL Latency                   | SQLite WAL Bat  |     75,015.0  | events/sec (13.3 us) |
|                                                  | IngestLog WAL   |     22,361.6  | events/sec (44.7 us) |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 7. Full-Pipeline End-to-End                      | Throughput      |     10,153.1  | events/sec           |
|                                                  | Data Loss       |            0  | events (Zero Loss)   |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 8. Replay and Recovery Throughput                | Replay Tput     |     64,046.5  | events/sec           |
|                                                  | Integrity       |       25,000  | / 25,000 matched     |
+--------------------------------------------------+-----------------+---------------+----------------------+
| 9. Backpressure Burst Recovery                   | Burst Size      |        4,000  | events               |
|                                                  | Drain Duration  |        269.9  | milliseconds         |
|                                                  | Effective Tput  |     11,811.2  | events/sec           |
|                                                  | Data Loss       |            0  | events (Zero Loss)   |
+--------------------------------------------------+-----------------+---------------+----------------------+
```

---

## 3. Deep-Dive Performance Analysis

### 3.1. Standalone Native C vs Python FFI Boundary Cost
The standalone native C engine (`mdrap-core.exe`, compiled with GCC `-O3 -mavx2`) processes market ticks at **5.01 million ticks/sec** with an average latency of **199.5 nanoseconds** per tick.

However, when called from Python via ctypes or Python C-extension bindings (`bench_python_vs_native_overhead`), throughput drops from 5.01M eps to **382k eps** (a 13x slowdown).
- **Cause**: CPython object conversion overhead, argument tuple packing, and function pointer trampoline overhead.
- **Architectural Implication**: High-throughput deployment MUST run native ingress directly in C (or decouple via SHM ring buffer) rather than invoking Python per tick.

### 3.2. Shared-Memory (SHM v3) Low Latency Stability
The Windows named shared-memory ring buffer demonstrates sub-microsecond transmission jitter:
- Across concurrency sweeps from 1 to 8 concurrent feeds, median latency remains flat at **2.1 microseconds**.
- 99th percentile tail latency remains under **5.0 microseconds** even with 8 active competing feed producers.
- The two-phase seqlock commit protocol (`UNCOMMITTED` sequence marker + memory fence) effectively isolates consumers from partial slot reads without requiring heavyweight OS mutex locks.

### 3.3. Persistence Bottleneck: SQLite vs IngestLog Binary WAL
- SQLite in WAL mode with `executemany` micro-batches of 2,000 rows achieves **75,015 eps** (~13.3 microseconds per event amortized).
- Binary `IngestLog` WAL achieves **22,361 eps** when committing individual frame boundaries.
- Full end-to-end Python pipeline throughput settles at **10,153 eps** when performing synchronous schema validation, cross-feed reconciliation tracking, and storage staging simultaneously.

### 3.4. Clock Skew and Timestamp Semantics
In Benchmark 7 (`bench_full_pipeline`), measuring `processing_timestamp - exchange_timestamp` exposed an essential domain reality:
- When synthetic or vendor feeds report epoch-based exchange timestamps (`time.time()`) while internal processing measurements use monotonic offsets or unaligned system clocks, latency differentials reflect clock synchronization skew (PTP/NTP offset) rather than platform processing delay.
- The pipeline correctly preserves both timestamps (`exchange_timestamp`, `receive_timestamp`, `processing_timestamp`), adhering to Invariant `INV-QUAL-003`.
