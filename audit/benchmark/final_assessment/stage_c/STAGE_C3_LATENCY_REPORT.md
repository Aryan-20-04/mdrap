# MDRAP — Stage C3: Latency Benchmark Report

**Document Reference:** `audit/benchmark/final_assessment/stage_c/STAGE_C3_LATENCY_REPORT.md`  
**Execution Timestamp:** 2026-11-22T07:44:48Z (2026-11-22T13:14:48+05:30 Local)  
**Host Environment:** Windows 11 Enterprise AMD64 (10 physical cores, 12 logical cores, 15.72 GB RAM) | Python 3.13.1  
**Git Branch:** `main` | **HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Raw Evidence Artifact:** [`audit/benchmark/final_assessment/stage_c/raw/stage_c_benchmarks.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/benchmark/final_assessment/stage_c/raw/stage_c_benchmarks.json)  
**Governance:** STRICT READ-ONLY INFRASTRUCTURE — NO DEPLOYMENT

---

## 1. Executive Summary

Stage C3 performed an audit and measurement of platform latency across all four system boundaries:
$$\text{Ingress} \longrightarrow \text{Engine} \longrightarrow \text{Transport} \longrightarrow \text{End-to-End}$$

All durations were captured using `MonotonicLatencyTracker` backed by `time.perf_counter_ns()`, completely eliminating wall-clock time drift, leap seconds, and NTP synchronization steps.

> [!IMPORTANT]
> **Reporting Governance Rule**:
> - Sampled percentiles are strictly reported separately from the exact all-event maximum.
> - **Sub-millisecond claims based solely on p50 are prohibited**: While median (p50) end-to-end latency is sub-millisecond (794.1 $\mu$s), the 95th percentile is 1,871.1 $\mu$s, the 99th percentile is 3,641.4 $\mu$s, and the all-event maximum reached 115.2 ms due to garbage collection and OS thread scheduling.

---

## 2. Clock Source Measurement & Overhead Distribution

To guarantee measurement precision, the resolution and overhead of `time.perf_counter_ns()` was profiled across 99,999 consecutive calls:

| Profiling Metric | Value (Nanoseconds) | Value (Microseconds) |
| :--- | :---: | :---: |
| **Call Count** | 99,999 iterations | — |
| **Median (p50)** | **100 ns** | **0.10 $\mu$s** |
| **90th Percentile (p90)** | **200 ns** | **0.20 $\mu$s** |
| **95th Percentile (p95)** | **200 ns** | **0.20 $\mu$s** |
| **99th Percentile (p99)** | **200 ns** | **0.20 $\mu$s** |
| **99.9th Percentile (p99.9)** | **1,600 ns** | **1.60 $\mu$s** |
| **Maximum Overhead** | **183,000 ns** | **183.00 $\mu$s** |
| **Mean Call Duration** | **153.29 ns** | **0.15 $\mu$s** |

**Conclusion:** Clock overhead is negligible relative to microsecond-scale pipeline stages (median overhead represents <0.02% of total pipeline latency).

---

## 3. Multi-Stage Latency Distribution (25,000 Events)

A continuous stream of **25,000 instrumented market events** was processed across the full stack.

### Latency Summary Table (in microseconds, $\mu$s)

| Stage | Sample Count | Invalid Count | Mean ($\mu$s) | Min ($\mu$s) | p50 ($\mu$s) | p90 ($\mu$s) | p95 ($\mu$s) | p99 ($\mu$s) | p99.9 ($\mu$s) | Sampled Max ($\mu$s) | All-Event Max ($\mu$s) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **Ingress** | 25,000 | 0 | 5.41 | 1.2 | **5.0** | 7.5 | 8.3 | 11.7 | 54.0 | 536.9 | **536.9** |
| **Engine** | 25,000 | 0 | 437.27 | 73.3 | **376.1** | 612.7 | 718.5 | 1,096.7 | 3,019.3 | 114,536.9 | **114,536.9** |
| **Transport** | 25,000 | 0 | 515.18 | 81.6 | **381.0** | 762.5 | 1,352.1 | 3,120.8 | 6,171.7 | 11,694.6 | **11,694.6** |
| **End-to-End** | 25,000 | 0 | 957.86 | 163.6 | **794.1** | 1,377.1 | 1,871.1 | 3,641.4 | 6,691.2 | 115,237.1 | **115,237.1** |

---

## 4. Verification of Telescoping Additive Identity

For every individual event, the telescoping identity was audited:
$$\Delta = |(\text{Latency}_{\text{ingress}} + \text{Latency}_{\text{engine}} + \text{Latency}_{\text{transport}}) - \text{Latency}_{\text{e2e}}| \le 0.05\,\mu\text{s}$$

- **Total Evaluated Events:** 25,000
- **Discrepancies:** **0**
- **Telescoping Identity Compliance:** **100.00%**
- **Mathematical Integrity:** Verified. The sum of the component stages strictly equals the measured end-to-end duration.

---

## 5. Single-Count Anomaly Accounting Audit

To verify that malformed timestamps or reverse-order clock steps do not bypass detection, synthetic anomalies were injected into the tracker:
1. Inverted Ingress: `ingest_ts_ns` < `t0_ns`
2. Inverted Engine: `broadcast_ts_ns` < `ingest_ts_ns`
3. Inverted Transport: `t_recv_ns` < `broadcast_ts_ns`

### Result:
- `ingress_invalid_count`: **1**
- `engine_invalid_count`: **1**
- `transport_invalid_count`: **1**
- `e2e_invalid_count`: **0**
- Every invalid measurement was rejected from reservoir percentiles and recorded in its respective stage's `invalid_count` counter.

---

## 6. Sampling Methodology

- **Reservoir Sampler:** Vitter's Algorithm R with a fixed capacity of 50,000 items ($O(K)$ space bound).
- **Exact All-Event Tracking:** Maintained concurrently via atomic `all_event_max_ns` tracking, guaranteeing that extreme outlier spikes are never discarded by reservoir downsampling.

---

## 7. Stage C3 Verdict

**VERDICT: VERIFIED**

Monotonic latency tracking across Ingress, Engine, Transport, and End-to-End boundaries operates with 100% mathematical telescoping consistency and zero invalid measurement leaks.
