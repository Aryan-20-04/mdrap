# MDRAP Phase 2 — Latency Stability & Drift Analysis

**Audit Date**: 2026-11-21  
**Target Environment**: Windows 11 Enterprise (AMD64, 12 logical cores, Intel Core @ 1.70 GHz, 15.72 GB RAM), Python 3.13.1  
**Git HEAD Commit**: `51f99e8e18768860540b534297b02b507cefce05` (`main`)  
**Data Sources**: `audit/benchmark/phase2/raw/soak_30min.json`, `audit/benchmark/raw/transport_memory_microbench.json`, `audit/benchmark/raw/ten_boundaries.json`

---

## 1. Executive Summary: Latency Predictability in Market Data Streaming

In institutional trading systems, median latency (p50) is insufficient; tail latency (p99, p99.9) and long-term latency drift determine queue buildup and data staleness.

During the 30-minute reliability soak test (**1,768,720 events**):
- **Overall Median Latency (p50)**: **447.99 µs** (sub-millisecond across the entire run).
- **99th Percentile Tail Latency (p99)**: **2,057.08 µs** (~2.05 ms).
- **Long-Duration Latency Drift**: Contouring the initial 10 minutes against the final 10 minutes revealed a net drift of only **+7.30 µs (p50)** and **+50.49 µs (p99)**.

### Latency Stability Verdict: **PASS (Sub-Millisecond Median, Negligible Tail Drift)**

---

## 2. Global Latency Percentile Distribution (30-Minute Soak)

Calculated across all $1.77 \times 10^6$ recorded roundtrips from injection to Observer state registration:

| Percentile Metric | Latency (µs) | Latency (ms) | Target SLA | Compliance Status |
|:---|:---:|:---:|:---:|:---:|
| **p50 (Median)** | **447.99 µs** | 0.448 ms | < 1,000.0 µs | **PASS (2.23x margin)** |
| **p90** | **957.49 µs** | 0.957 ms | < 2,000.0 µs | **PASS (2.09x margin)** |
| **p95** | **1,231.43 µs** | 1.231 ms | < 3,000.0 µs | **PASS (2.44x margin)** |
| **p99 (Tail)** | **2,057.08 µs** | 2.057 ms | < 5,000.0 µs | **PASS (2.43x margin)** |
| **p99.9 (Extreme Tail)**| **4,788.40 µs** | 4.788 ms | < 10,000.0 µs | **PASS (2.09x margin)** |
| **Max Peak Latency** | **46,065.09 µs**| 46.065 ms | < 100.0 ms | **PASS (GC Invariant)** |

---

## 3. Ten-Minute Window Drift Analysis

To detect whether queue backpressure, cache eviction, or memory pressure caused degradation over time, latency percentiles were averaged across the first 10 minutes and compared with the final 10 minutes:

| Latency Metric | Initial 10 Minutes (M01–M10) | Final 10 Minutes (M21–M30) | Absolute Drift | Relative Drift |
|:---|:---:|:---:|:---:|:---:|
| **Average p50** | **446.32 µs** | **453.62 µs** | **+7.30 µs** | **+1.64%** |
| **Average p90** | **898.42 µs** | **912.18 µs** | **+13.76 µs** | **+1.53%** |
| **Average p95** | **1,225.13 µs** | **1,244.76 µs** | **+19.63 µs** | **+1.60%** |
| **Average p99** | **1,941.78 µs** | **1,992.27 µs** | **+50.49 µs** | **+2.60%** |

### Key Finding on Drift
Across 1,768,720 events spanning 30 minutes, median latency varied by only **7.3 microseconds**, and 99th percentile tail latency varied by only **50.5 microseconds**. This demonstrates that queue draining and socket polling remained in lockstep with the producer, with zero backpressure accumulation.

---

## 4. Minute-by-Minute Tail Latency Stability

```
Latency Percentile Progression Across 30 Minutes (µs)
  3000 ┼                                                                      
  2500 ┼  ╭──╮     ╭─╮                                ╭─╮                     [p99 Tail]
  2000 ┼──╯  ╰─────╯ ╰─╯───╰───╯────╰───╯────╰───╯────╯ ╰─╯────╰───╯───       (~2.0 ms)
  1500 ┼                                                                      
  1000 ┼───────────────────────────────────────────────────────────────       [p95] (~1.2 ms)
   500 ┼───────────────────────────────────────────────────────────────       [p50] (~0.45 ms)
     0 ┼───────────────────────────────────────────────────────────────
       M01   M04   M07   M10   M13   M16   M19   M22   M25   M28   M30
```

- **p50 Invariance**: The median latency remained within a tight band between **389.6 µs and 502.6 µs** across every single 60-second window.
- **p99 Predictability**: The 99th percentile latency held between **1,596.0 µs and 2,605.0 µs**, never exceeding 2.65 ms across 30 distinct sampling windows.
- **Max Latency Spikes**: The maximum single-event latency observed was **46.06 ms**, corresponding to a major OS file flush and GC generation-2 cycle, well below the 100 ms institutional threshold.

---

## 5. Transport Latency Comparison: TCP vs. Shared Memory

When ultra-low latency is required, MDRAP supports zero-copy Shared Memory (SHM v3):

| Metric | Streaming TCP Socket | Seqlock Shared Memory (SHM v3) | Latency Advantage |
|:---|:---:|:---:|:---:|
| **Uncontended Transit p50**| 46.5 µs | **4.8 µs** | **SHM is 9.7x faster** |
| **Uncontended Transit p99**| 168.0 µs | **9.9 µs** | **SHM is 17.0x faster** |
| **Multi-Consumer Fanout** | Socket queue contention | Lock-free concurrent read | **SHM eliminates queue lag** |
