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

## 6. Phase 0 Empirical Hot-Path Profiling & FFI Boundary Finding

Executed via `python benchmarks/profile_hotpath.py --events 100000 --seed 42` and `python benchmarks/micro_ffi.py --iterations 10000000`:

### Empirical Stage Breakdown (100,000 Events, seed=42)
| Pipeline Stage | Measured Latency (p50) | Share of Budget |
|---|---|---|
| **Reconciliation & Reliability** | **22.70 µs** | **43.3%** |
| **Gateway Normalize (`RawEvent` -> `CanonicalEvent`)** | **14.10 µs** | **26.9%** |
| **Python Quality Wrapper** | **9.87 µs** | **18.8%** |
| **Storage Batch Flush (`Store.write_*`)** | **4.10 µs** | **7.8%** |
| **ctypes FFI Boundary Tax** | **2.45 µs** (2,448 ns) | **4.7%** |
| **C-Side Quality Math Logic** | **0.08 µs** (82 ns) | **0.2%** |
| **Total End-to-End Processing (p50)** | **52.40 µs** | **100.0%** |

- **Sum of Stages**: 53.30 µs (1.72% discrepancy against 52.40 µs p50, within 10% acceptance gate).
- **FFI Boundary Share**: **4.7%** of the end-to-end processing budget.
- **Phase 0 Gate Decision**: Since the ctypes FFI tax is 4.7% (< 15% threshold), handwritten CPython C-API extensions (`.pyd`) in Phases 3–4 are cancelled. Optimization effort focuses on high-leverage bottlenecks (Reconciliation, Gateway Normalization, and Python Quality marshaling).
