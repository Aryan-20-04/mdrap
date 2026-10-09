# MDRAP Phase 6 — Profiling Methodology & Measurement Framework

## 1. Executive Summary & Purpose
Accurate performance measurement in sub-millisecond systems requires eliminating measurement noise, wall-clock jitter, and synthetic distortion. This document details the profiling tools, timing primitives, and operating regimes evaluated in Phase 6.

---

## 2. Timing Primitives and Profiling Tooling

- **High-Resolution Clock**: Python `time.perf_counter()` backed by OS high-resolution timers (`QueryPerformanceCounter` on Win32, `clock_gettime(CLOCK_MONOTONIC)` on POSIX), yielding sub-microsecond resolution ($< 100\text{ ns}$).
- **Memory Tracking**: Standard library `tracemalloc` to record baseline RSS, peak heap allocation, and allocation delta during burst runs.
- **Microsecond Profiling Wrapper**: Event dispatch timestamps captured at entry ($T_0$) and exit ($T_1$) to measure exact queue dwell time:
  $$\Delta_{\text{dispatch}} = (T_1 - T_0) \times 10^6 \quad (\mu\text{s})$$

---

## 3. Evaluated Operating Conditions

| Regime | Workload Configuration | Primary Monitored Metric |
| :--- | :--- | :--- |
| **1. Idle Baseline** | 0 incoming events; background timers only | CPU idle draw (< 0.1%), stable RSS |
| **2. Normal Sustained** | 3,166 events / sec (Phase 5 baseline) | Steady-state p50 latency (~278 µs) |
| **3. Burst Ingestion** | 20,000 events burst across 10 symbols | Queue absorption, peak throughput (19,890 eps) |
| **4. Saturation** | 50,000 events flooded into bounded queue | Queue full behavior, backpressure throttle |
| **5. Slow Consumer** | Consumer reading at 10% rate of producer | Bounded queue drop count, auto-eviction at 10 drops |
| **6. Persistence Stall**| Synchronous `os.fsync()` per event | Storage latency stall detection |
| **7. Crash Recovery** | Termination during active write; replay | Recovery time objective (< 2.0s) |
| **8. Multi-Instance Fleet**| 2-shard symbol range partitioning | Linear throughput scaling, zero cross-shard lock |
