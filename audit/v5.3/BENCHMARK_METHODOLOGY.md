# MDRAP v5.3 — Independent Benchmark Methodology & Traceability

## Standards & Principles
All benchmark measurements in MDRAP v5.3 adhere to strict institutional guidelines:
1. **Measured Before Claimed**: Every reported number is backed by executable test scripts and machine-readable JSON logs in `audit/v5.3/raw/`.
2. **Explicit Architectural Boundaries**: Throughput numbers specify the exact subsystem boundary measured (Stateless Ingress vs Stateful Pipeline).
3. **Tail Latency Reporting**: Always report p50, p90, p95, p99, p99.9, and Max—never raw averages alone.
4. **Deterministic Reproducibility**: Fixed PRNG seeds (`seed=42`), clean process environments, and isolated CPU cores.
5. **Cold vs Warm Distinction**: First 500 events treated as cache warm-up; tail statistics measured over sustained operating steady-state.

---

## 1. Architectural Pipeline Boundaries (A through I)

The unified benchmark harness (`benchmarks/run_v53_unified_benchmark.py`) isolates each boundary independently:

- **Boundary A (Ingress)**: Raw JSON string generation and stream deserialization.
- **Boundary B (Normalization)**: Translation of raw venue dictionary into strongly typed `CanonicalEvent`.
- **Boundary C1 (Native C Fastpath)**: C validation kernel evaluating price corridor, sequence, staleness, and bitmask packing.
- **Boundary C2 (Pure Python Fastpath)**: Equivalent Python fallback validating the exact same event stream with `force_pure=True`.
- **Boundary D (Strategy Evaluation)**: Closed-form microstructure models (Avellaneda-Stoikov and Linear Inventory Skew).
- **Boundary E (State Transitions & Journaling)**: Stateful sequence bitmap updates and undo journal allocation in `Engine.step()`.
- **Boundary F (SHM Ring Roundtrip)**: Seqlock write into POSIX/Windows shared memory ring buffer and immediate consumer read.
- **Boundary G (Persistence)**: Disk WAL commit throughput under `never`, `grouped_by_time`, and `always`.
- **Boundary H (Full Stateful E2E)**: End-to-end ingestion from raw JSON $\to$ schema normalize $\to$ `engine.step()` $\to$ SHM ring write.
- **Boundary I (Multicore Scaling)**: Parallel worker scaling across 1, 2, 4, and 8 CPU processes.

---

## 2. Traceability Map

All raw performance data in this release traces to:
- Benchmark Harness: `benchmarks/run_v53_unified_benchmark.py`
- Raw Telemetry: `audit/v5.3/raw/benchmarks_raw.json`
- Multicore Telemetry: `audit/v5.3/raw/multicore_scaling.json`
- Quantitative Strategy Telemetry: `audit/v5.3/raw/quantitative_audit.json`
- Fault Injection Telemetry: `audit/v5.3/raw/fault_injection_results.json`
