# MDRAP v5.3 — Native C Optimization & Benchmarking Results

## Scope & Target
This document details the micro-benchmarking results of native C code execution compared across versions v5.0, v5.1, v5.2, and v5.3.

---

## 1. Longitudinal Version Comparison

| Metric | v5.0 Baseline | v5.1 Optimization | v5.2 Certified | v5.3 Re-Audited |
| :--- | :--- | :--- | :--- | :--- |
| **Commit Target** | `f2e6f80` | `fb8bb8a` | `fb8bb8a` | `3af18f5` (v5.3) |
| **Native Throughput** | 412,400 EPS | 580,200 EPS | 619,744 EPS | **510,820 EPS** |
| **Python Fallback** | 102,300 EPS | 125,400 EPS | 625,851 EPS* | **170,080 EPS** |
| **True Speedup** | 4.03x | 4.63x | 0.99x (Invalid)* | **3.00x** |
| **p50 Latency (Native)**| 2.20 $\mu$s | 1.80 $\mu$s | 1.55 $\mu$s | **1.60 $\mu$s** |
| **p99 Latency (Native)**| 8.40 $\mu$s | 5.20 $\mu$s | 4.20 $\mu$s | **4.10 $\mu$s** |
| **p99 Latency (Python)**| 32.10 $\mu$s | 24.50 $\mu$s | 4.15 $\mu$s* | **18.00 $\mu$s** |

*\*Note on v5.2: The reported Python throughput of 625,851 EPS was an experimental artifact caused by the `force_pure` absorption bug, which actually executed the C fastpath twice.*

---

## 2. Micro-Benchmark Analysis

### 2.1 Latency Distribution Percentiles (v5.3 Verified)
Measured over 30,000 randomized normalized feed events:

```
[Native C Fastpath]
  Min:       0.90 µs
  p50:       1.60 µs
  p90:       2.70 µs
  p95:       3.00 µs
  p99:       4.10 µs
  p99.9:    27.50 µs
  Max:     158.20 µs

[Pure Python Engine]
  Min:       2.80 µs
  p50:       4.50 µs
  p90:      10.30 µs
  p95:      13.30 µs
  p99:      18.00 µs
  p99.9:    88.20 µs
  Max:     794.50 µs
```

### 2.2 Observations
1. **Low Tail Dispersion**: Native C maintains exceptional jitter control: 99% of ticks are evaluated in $\le 4.10\,\mu\text{s}$.
2. **Python GC Pauses**: Pure Python suffers from periodic garbage collection pauses, reaching a worst-case latency spike of $794.50\,\mu\text{s}$ ($5.0\text{x}$ higher than Native C max).
3. **Deterministic Memory Footprint**: Native C maintains a fixed memory footprint of $64\text{ MB}$ for the pre-allocated static matrix table, eliminating runtime heap allocations during execution.
