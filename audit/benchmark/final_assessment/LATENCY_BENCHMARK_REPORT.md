# MDRAP — Multi-Stage Latency & Monotonic Identity Benchmark Report

**Document Reference:** `audit/benchmark/final_assessment/LATENCY_BENCHMARK_REPORT.md`  
**Execution Timestamp:** 2026-11-22T01:45:00 UTC  
**Environment:** Windows 11 Enterprise AMD64 | Intel Core i7 (10 physical / 12 logical cores) | Python 3.13.1  
**Repository Branch:** `main`  
**Git HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Clean Release Reference:** `maintenance/architecture-pruning-v608-clean` (`38ec051e0958c2199268a03afb54f6f24b89cd58`)  
**Raw Evidence Artifact:** [`audit/benchmark/final_assessment/raw/benchmark_stage_c.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/benchmark/final_assessment/raw/benchmark_stage_c.json)  
**Governance:** STRICT READ-ONLY INFRASTRUCTURE — NO DEPLOYMENT — NO LIVE EXECUTION

---

## 1. Executive Summary

This report delivers the high-resolution, monotonic latency audit of the complete MDRAP pipeline across all physical and architectural boundaries:
1. **Ingress Stage ($\Delta t_{\text{ingress}}$):** Physical network buffer arrival to sequencer loop entry ($t_0 - t_{\text{ingest}}$).
2. **Engine Stage ($\Delta t_{\text{engine}}$):** Quality engine validation, BBO synthesis, and internal queueing ($t_{\text{bcast}} - t_0$).
3. **Transport Stage ($\Delta t_{\text{transport}}$):** JSON serialization, TCP loopback socket traversal, and client socket read ($t_{\text{recv}} - t_{\text{bcast}}$).
4. **End-to-End Pipeline ($\Delta t_{\text{e2e}}$):** Complete path from raw event ingress to Observer reception ($t_{\text{recv}} - t_{\text{ingest}}$).

All measurements were captured using `time.perf_counter_ns()` with zero wall-clock subtractions, eliminating NTP step errors and leap-second distortions.

> [!WARNING] **Tail Latency Disclosure & System Classification**
> While the median end-to-end latency is sub-millisecond (**p50 = 810.0 µs**), institutional trading standards strictly prohibit classifying a system as "sub-millisecond" based solely on median latency. Tail latencies exceed 1 ms: **p95 = 1,780.9 µs (1.78 ms)**, **p99 = 3,144.5 µs (3.14 ms)**, and **All-Event Maximum = 110,272.0 µs (110.27 ms)** due to operating system socket scheduling and Python interpreter execution jitter.

---

## 2. Multi-Stage Latency Percentile Distribution

The following distribution was calculated across **25,000 live streaming events** sampled uniformly using a 50,000-capacity reservoir sampler:

| Pipeline Stage | Sample Count | Invalid Count | Mean ($\mu\text{s}$) | Min ($\mu\text{s}$) | p50 ($\mu\text{s}$) | p90 ($\mu\text{s}$) | p95 ($\mu\text{s}$) | p99 ($\mu\text{s}$) | p99.9 ($\mu\text{s}$) | All-Event Max ($\mu\text{s}$) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **Ingress ($\Delta t_{\text{ingress}}$)** | 25,000 | 0 | 4.72 | 1.3 | **4.4** | 6.5 | 7.2 | 9.7 | 37.0 | **500.9** |
| **Engine ($\Delta t_{\text{engine}}$)** | 25,000 | 0 | 407.19 | 74.5 | **358.2** | 569.3 | 677.6 | 1,046.7 | 1,850.9 | **109,673.2** |
| **Transport ($\Delta t_{\text{transport}}$)** | 25,000 | 0 | 514.79 | 145.9 | **395.5** | 828.1 | 1,391.9 | 2,823.4 | 4,869.8 | **11,158.6** |
| **End-to-End ($\Delta t_{\text{e2e}}$)** | 25,000 | 0 | 926.71 | 311.0 | **810.0** | 1,321.1 | 1,780.9 | 3,144.5 | 5,539.4 | **110,272.0** |

---

## 3. Clock Overhead & Measurement Precision

To establish the noise floor of the benchmarking harness, 99,999 back-to-back invocations of `time.perf_counter_ns()` were benchmarked:

| Statistic | Nanoseconds (ns) | Microseconds ($\mu\text{s}$) | Interpretation |
| :--- | :-: | :-: | :--- |
| **p50 Overhead** | **200 ns** | 0.20 µs | Hardware TSC clock read latency |
| **p90 Overhead** | **200 ns** | 0.20 µs | Zero cache misses |
| **p95 Overhead** | **200 ns** | 0.20 µs | Highly consistent timing |
| **p99 Overhead** | **300 ns** | 0.30 µs | Minor Python opcode boundary jitter |
| **p99.9 Overhead** | **2,200 ns** | 2.20 µs | Thread preemption boundary |
| **Maximum Overhead** | **218,300 ns** | 218.30 µs | OS context switch interval |
| **Average Overhead** | **206.68 ns** | 0.21 µs | Measurement overhead is $<0.03\%$ of p50 latency |

**Conclusion:** Clock measurement overhead introduces less than 0.21 µs of average distortion, confirming that stage latencies reflect genuine pipeline execution rather than profiling harness artifacts.

---

## 4. Verification of the Telescoping Additive Identity

For multi-stage latency telemetry to be mathematically self-consistent, every single event must satisfy the telescoping identity:

$$\Delta t_{\text{e2e}} = \Delta t_{\text{ingress}} + \Delta t_{\text{engine}} + \Delta t_{\text{transport}}$$

### Empirical Verification Results:
- **Total Valid Events Evaluated:** `25,000`
- **Events Satisfying Identity:** `25,000`
- **Telescoping Discrepancies:** `0`
- **Identity Compliance Ratio:** **100.0000%**

---

## 5. Anomaly Accounting & Single-Count Invalidation Verification

Following the clean 25,000 event run, 3 synthetic clock anomaly events were injected to verify that inverted timestamps ($\Delta t < 0$) are quarantined:
- Injected Event 1: Inverted ingress boundary ($t_0 < t_{\text{ingest}}$) -> `ingress.invalid_count == 1`.
- Injected Event 2: Inverted engine boundary ($t_{\text{bcast}} < t_0$) -> `engine.invalid_count == 1`.
- Injected Event 3: Inverted transport boundary ($t_{\text{recv}} < t_{\text{bcast}}$) -> `transport.invalid_count == 1`.

**Results:**
- Each invalid observation was counted **exactly once** in its respective stage metric.
- Zero invalid durations entered the reservoir sampler or polluted percentile calculations.
- Clean events recorded 0 invalid counts.

---

## 6. Certification Verdict

The Latency Benchmark confirms:
- **Median E2E Latency:** 810.0 µs.
- **Tail E2E Latency:** p95 = 1.78 ms, p99 = 3.14 ms, Max = 110.27 ms.
- **Telescoping Identity:** 100% verified across 25,000 events.
- **Monotonic Integrity:** 100% verified.

---
*Report certified under MDRAP Governance — Read-Only Market-Data Infrastructure — No Deployment.*
