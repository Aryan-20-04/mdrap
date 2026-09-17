# MDRAP Benchmark Methodology

Based on the *Market Data Reliability & Acceleration Platform Reference (Section 9, 10, 11, 12, 13, 21)*.

## 1. Guiding Principles

1. **Measure before claiming**: No performance claims without empirical JSON log files.
2. **Deterministic workloads**: Fixed pseudo-random seeds (`seed=42`) ensure byte-level replayability.
3. **Ground-truth scoring**: Compare detected faults directly against injected fault labels from the simulator.
4. **Tail latency focus**: p50, p95, p99, and p99.9 are mandatory metrics.

---

## 2. Benchmark Harness Execution (`cli.py benchmark`)

```bash
# Standard 1,000,000-event benchmark
python cli.py benchmark --events 1000000 --seed 42 --label baseline_v1

# Benchmark with cProfile breakdown
python cli.py benchmark --events 100000 --seed 42 --profile --label profile_v1
```

### Metrics Recorded
- **Throughput**: Sustained events per second.
- **End-to-End Latency**: Time from simulated event exchange timestamp to database commit.
- **Processing Latency**: Time spent purely in normalization, quality checking, and reconciliation.
- **Quality Precision & Recall**:
  - Detection Rate: `detected_known_errors / actual_injected_errors`
  - False Positive Rate: `valid_events_incorrectly_flagged / total_valid_events`
- **Environment Metadata**: OS, Python runtime version, commit / timestamp, CPU architecture.

---

## 3. Load Testing Protocol (`cli.py loadtest`)

```bash
python cli.py loadtest --levels 10000,50000,100000,250000,500000
```
Measures throughput scaling and latency degradation curves across increasing event counts. Identifies SQLite batch saturation limits and queue growth boundaries.

---

## 4. Architectural Comparison Protocol (`cli.py compare`)

```bash
python cli.py compare --events 100000 --seed 42
```
Runs identical event workloads through three architectural tiers sequentially:
1. **V1 Baseline**: Synchronous pure Python pipeline with SQLite WAL batched persistence.
2. **V2 Streaming**: Decoupled async queue broker with high-watermark backpressure.
3. **V4 Native C**: FastPath compiled C hot path evaluation (`fastpath.dll`).

Outputs comparative side-by-side metrics: throughput (eps), elapsed time, p50/p95/p99 E2E latency, and false positive rates.

---

## 5. Vectorized SBE Throughput Benchmark (`cli.py throughput`)

```bash
# Benchmark 1,000,000 contiguous 128-byte SBE frames
python cli.py throughput -e 1000000 --compare
```
Measures pure hardware bus saturation and SIMD execution speed on contiguous SBE binary streams:
- **Throughput (MEPS)**: Peak sustained million events per second (exceeds 50M+ eps).
- **Sub-Microsecond Latency**: Nanoseconds per event (< 20 ns).
- **Memory Bandwidth**: Processing throughput in GB/sec across contiguous C memory buffers.
- **Ground-Truth Scored Accuracy**: Exact classification verification of crossed quotes, negative prices, and duplicate sequence numbers.

---

## 6. Phase 0 Empirical Hot-Path Profiling & Initial FFI Measurement

Executed via `python benchmarks/profile_hotpath.py --events 100000 --seed 42` and `python benchmarks/micro_ffi.py --iterations 10000000`:

### Empirical Stage Breakdown (100,000 Events, seed=42, Storage-Inclusive Instrumented Run)
| Pipeline Stage | Measured Latency (p50) | Share of Budget |
|---|---|---|
| **Reconciliation & Reliability** | **22.70 µs** | **43.3%** |
| **Gateway Normalize (`RawEvent` -> `CanonicalEvent`)** | **14.10 µs** | **26.9%** |
| **Python Quality Wrapper** | **9.87 µs** | **18.8%** |
| **Storage Batch Flush (`Store.write_*`)** | **4.10 µs** | **7.8%** |
| **ctypes FFI Boundary Tax (12 scalars)** | **2.45 µs** (2,448 ns) | **4.7%** |
| **C-Side Quality Math Logic** | **0.08 µs** (82 ns) | **0.2%** |
| **Total Instrumented Pipeline (p50)** | **52.40 µs** | **100.0%** |

- **Sum of Stages**: 53.30 µs (1.72% discrepancy against 52.40 µs p50, within 10% acceptance gate).

---

## 7. Phase 9R Audit: Denominator Reconciliations & FFI Tax Deconstruction

### 7.1 Denominator Definitions
To ensure complete transparency and prevent metric dilution, MDRAP benchmarks define three distinct latency denominators:

1. **Denominator 1: Full Pipeline (Storage-Inclusive Instrumented Profile)**
   - *Scope*: Ingress $\rightarrow$ Normalize $\rightarrow$ Quality $\rightarrow$ Reconciler $\rightarrow$ Storage Batch Flush + `perf_counter_ns` stage instrumentation overhead.
   - *Latency*: **52.40 µs** p50.
   - *FFI Share*: $2.45\,\mu\text{s} / 52.40\,\mu\text{s} = \mathbf{4.7\%}$.
   - *Finding*: Evaluated against this storage-inclusive instrumented baseline, FFI appeared diluted.

2. **Denominator 2: Uninstrumented Compute Path (`compare_v1_v4.py`)**
   - *Scope*: Ingress $\rightarrow$ Normalize $\rightarrow$ Quality $\rightarrow$ Reconciler $\rightarrow$ In-Memory Store (`:memory:`). Zero per-stage instrumentation timers.
   - *Latency*: **14.90 µs** p50 (V4) vs **19.00 µs** p50 (V1).
   - *FFI Share (12 scalars)*: $2.07\,\mu\text{s} / 14.90\,\mu\text{s} = \mathbf{13.89\%}$ (post-fix) or $2.07\,\mu\text{s} / 19.00\,\mu\text{s} = \mathbf{10.89\%}$ (pre-fix).
   - *FFI Share (Struct-by-pointer)*: $0.45\,\mu\text{s} / 14.90\,\mu\text{s} = \mathbf{3.02\%}$.

3. **Denominator 3: Quality Engine Stage Only (`micro_ffi.py`)**
   - *Scope*: Pure `fastpath_eval_fast` call (FFI boundary + C mathematical evaluation).
   - *Latency*: **2.13 µs** total.
   - *FFI Share*: $2.07\,\mu\text{s} / 2.13\,\mu\text{s} = \mathbf{97.2\%}$ of the quality stage alone.

### 7.2 FFI Tax Deconstruction (`benchmarks/micro_ffi.py`)
Measured across 2,000,000 iterations $\times$ 5 timed runs against an empty C function (`fastpath_noop`):

| Calling Convention Variant | Net Overhead (ns/call) | Notes |
|---|---|---|
| **(i) Bare CDLL (no `argtypes`)** | **981.33 ns** | Requires pre-wrapped ctypes objects; dynamic type inference |
| **(ii) Pinned CDLL (12 scalar args)** | **2,072.12 ns** (~2.07 µs) | Pinned `argtypes`/`restype`; 12 Python $\rightarrow$ C scalar conversions |
| **(iii) `CFUNCTYPE` prototype (12 args)** | **2,175.54 ns** (~2.18 µs) | Direct function pointer prototype; marginal difference on Windows |
| **(iv) Struct-by-Pointer (1 arg)** | **452.04 ns** (~0.45 µs) | **4.58× faster boundary**; single pointer marshaling |
| **(v) Single Scalar Arg (1 int)** | **394.20 ns** (~0.39 µs) | Confirms scalar ctypes baseline is in expected 150–400 ns range |

#### Root Cause of the ~2 µs Tax
The 2.07 µs overhead is **not** an inherently broken calling convention. A single scalar ctypes call costs **394 ns** (matching expected 150–400 ns bounds). The 2.07 µs cost is the linear accumulation of marshaling **12 separate scalar arguments** ($12 \times \approx 150\,\text{ns}$). Passing a single pointer (`FastEvent*`) collapses this to **452 ns**, and micro-batching (`evaluate_batch`) amortizes it to **< 15 ns per event**.

### 7.3 Back-to-Back V1 vs V4 Reproducibility Proof
Re-measured back-to-back on identical machine and environment (`seed=42`, 7 runs, 2 warmup, 50,000 events):

```
Metric                         | V1 (Pure Python)   | V4 (Native C)     
----------------------------------------------------------------------
Processing Latency p50         |          19.00 µs  |          14.90 µs
p50 IQR (Spread)               |           1.05 µs  |           0.35 µs
p50 Q1 - Q3 Range              | 18.30 - 19.35 µs   | 14.75 - 15.10 µs
Throughput (median)            |       29,624.0 eps |       33,686.4 eps
----------------------------------------------------------------------
V4 beats V1 on p50:        YES (-21.6% latency reduction)
V4 beats V1 on throughput: YES (+13.7% throughput improvement)
Non-overlapping IQRs:      YES (14.75-15.10 µs vs 18.30-19.35 µs)
```

Reproducing command:
```bash
python benchmarks/compare_v1_v4.py --events 50000 --seed 42
```

### 7.4 Test Suite Count Reconciliation
- **Unique Test Function Definitions**: **676 distinct test functions** across 81 test files in `tests/`.
- **Pytest Collected by Default**: **679 fast tests** (including 3 seed parameterizations for differential parity).
- **Deselected Slow/Integration Tests**: **59 tests** marked `@pytest.mark.slow` / `@pytest.mark.integration`.
- **Total Test Cases Defined**: **738 tests**.
- *Stale Count Explanation*: The figure "238" was an early snapshot before tests for multi-venue calendar registries, symbology resolution, order book depth bands, transaction cost analysis (TCA), and portfolio risk were added.

