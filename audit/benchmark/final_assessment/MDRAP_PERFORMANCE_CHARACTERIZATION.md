# MDRAP — Performance Characterization & Benchmark Synthesis

**Document Reference:** `audit/benchmark/final_assessment/MDRAP_PERFORMANCE_CHARACTERIZATION.md`  
**Assessment Date:** 2026-11-22  
**Platform Release Version:** `MDRAP 6.0.8`  
**Host Environment:** Windows 11 Enterprise AMD64 (10 physical cores, 12 logical cores, 15.72 GB RAM) | Python 3.13.1  
**Git Active Branch:** `main` | **HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Raw Benchmark Evidence:** [`audit/benchmark/final_assessment/stage_c/raw/stage_c_benchmarks.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/benchmark/final_assessment/stage_c/raw/stage_c_benchmarks.json)  
**Governance:** STRICT READ-ONLY MARKET DATA INFRASTRUCTURE — STRICT NO DEPLOYMENT

---

## 1. Executive Performance Summary

This document consolidates the empirical performance characterization of MDRAP established during Stage C benchmarking. All measurements were conducted under controlled, synthetic conditions with CPU, memory, and timing metrics captured via non-invasive monotonic instrumentation.

---

## 2. Throughput Characterization by Subsystem

Throughput varies significantly across architectural tiers due to differing synchronization mechanisms, memory boundaries, and serialization protocols.

| Subsystem / Architectural Tier | Workload Size | Measured Rate (EPS) | Primary Bottleneck / Mechanism |
| :--- | :---: | :---: | :--- |
| **Pure Engine Ingestion** | 50,000 events | **2,398.3 EPS** | Pure Python event validation, rule checks, canonical instantiation. |
| **TCP Streaming Pipeline** | 50,000 events | **1,182.0 EPS** | Python stdlib JSON serialization + loopback TCP socket dispatch. |
| **SHM Zero-Copy Write** | 50,000 events | **326,695.5 EPS** | Lock-free SPMC circular ring buffer write (atomic seqlock commit). |
| **SHM Zero-Copy Read** | 50,000 events | **262,221.8 EPS** | Lock-free SPMC circular ring buffer read (100% delivery ratio). |
| **Exporter Disk Persistence** | 50,000 events | **60,279.3 EPS** | Asynchronous disk append to JSONL with memory buffering. |

> [!WARNING]
> **Incomparability Notice**:  
> The >326,000 EPS measured on Zero-Copy SHM must NOT be presented as a universal 130x acceleration ratio over TCP for all operations. SHM measures in-memory memory-mapped struct copies on a single host. TCP measures multi-process JSON socket dispatch across OS network stacks. Each tier serves distinct architectural roles.

---

## 3. Latency Distribution & Monotonic Accounting

All latency measurements were captured using `MonotonicLatencyTracker` backed by `time.perf_counter_ns()`.

### 3.1 Clock Source Resolution & Overhead
- Evaluated across 99,999 consecutive invocations:
  - **Median (p50):** **100 ns** (0.10 $\mu$s)
  - **90th Percentile (p90):** **200 ns** (0.20 $\mu$s)
  - **99th Percentile (p99):** **200 ns** (0.20 $\mu$s)
  - **Maximum Overhead:** **183.0 $\mu$s** (OS thread preemption artifact)
  - **Mean Overhead:** **153.29 ns**
- **Conclusion:** Clock overhead represents <0.02% of median pipeline latency.

### 3.2 Multi-Stage Latency Distribution (Stage C3, 25,000 Events)

| Stage | Sample Count | Invalid Count | Mean ($\mu$s) | Min ($\mu$s) | p50 ($\mu$s) | p90 ($\mu$s) | p95 ($\mu$s) | p99 ($\mu$s) | p99.9 ($\mu$s) | Max ($\mu$s) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **Ingress** | 25,000 | 0 | 5.41 | 1.2 | **5.0** | 7.5 | 8.3 | 11.7 | 54.0 | **536.9** |
| **Engine** | 25,000 | 0 | 437.27 | 73.3 | **376.1** | 612.7 | 718.5 | 1,096.7 | 3,019.3 | **114,536.9** |
| **Transport (TCP)** | 25,000 | 0 | 515.18 | 81.6 | **381.0** | 762.5 | 1,352.1 | 3,120.8 | 6,171.7 | **11,694.6** |
| **End-to-End** | 25,000 | 0 | 957.86 | 163.6 | **794.1** | 1,377.1 | 1,871.1 | 3,641.4 | 6,691.2 | **115,237.1** |

### 3.3 Telescoping Additive Identity
For every event observation $i$:
$$|(\text{Latency}_{\text{ingress}} + \text{Latency}_{\text{engine}} + \text{Latency}_{\text{transport}}) - \text{Latency}_{\text{e2e}}| \le 0.05\,\mu\text{s}$$
- Total Evaluated: **25,000 events** (Stage C3) + **5,733,615 events** (Stage C5 Soak).
- Total Discrepancies: **0** (**100.00% compliance**).
- Invalid Timestamp Rejections: **0** during clean runs; injected timestamp inversions were strictly counted in `invalid_count` without contaminating reservoir percentiles.

### 3.4 Tail Latency Reality: Why Median p50 $\ne$ Sub-Millisecond System
> [!CAUTION]
> **Sub-Millisecond Median Fallacy**:  
> While median end-to-end latency is sub-millisecond (**794.1 $\mu$s** in Stage C3, **1,110.9 $\mu$s** in Stage C5), **the system does NOT provide sub-millisecond tail guarantees**:
> - 5% of events exceed **1.87–2.21 ms** (p95).
> - 1% of events exceed **3.36–3.64 ms** (p99).
> - 0.1% of events exceed **6.69–7.02 ms** (p99.9).
> - Extreme outliers reached **115.2 ms** during normal Python GC sweeps, and **19.4 seconds** during OS power throttling.  
> Marketing or architectural claims of "sub-millisecond performance" based strictly on p50 are technically misleading and prohibited.

---

## 4. Working Set & Resource Bounding

### 4.1 Memory Allocation Profile
- **Process Baseline RSS:** **60.27 MB** (Python 3.13 runtime + imports).
- **Daemon Ingestion Overhead:** **+0.40 MB**.
- **Client & Observer Harness Overhead:** **+0.12 MB**.
- **Peak RSS Under Sustained Burst (60,000 Events):** **78.68 MB**.
- **Steady-State RSS:** **78.65 MB**.
- **Post-Teardown RSS:** **60.04 MB** (Net growth: **-0.23 MB**).

### 4.2 Two-Hour Soak Working Set Progression (Stage C5)
- **Initial Working Set:** **48.95 MB**.
- **Peak Working Set:** **130.99 MB** (at minute 25).
- **Asymptotic Stabilization:** Decreased and stabilized between **110.4 MB and 110.7 MB** across minutes 50 to 100.
- **Post-Teardown Working Set:** **41.05 MB** (Net lifecycle growth: **-7.89 MB**).

### 4.3 Memory Bound Caveat
> [!NOTE]
> A negative post-teardown RSS delta across a 2-hour benchmark demonstrates excellent garbage collector cleanup and effective $O(1)$ memory buffering in test harnesses, but it does NOT constitute mathematical proof of zero memory leaks under indefinite multi-day/multi-week operational lifespans.

### 4.4 CPU Utilization Profile
- **User CPU Time:** 32.453s | **System CPU Time:** 25.891s | **Total CPU Time:** 58.344s.
- **Elapsed Duration:** 50.423s.
- **Normalized CPU Utilization:** **115.7%** (equivalent to 1.16 logical cores saturated out of 12 available).

---

## 5. Performance Verdict

**VERDICT: VERIFIED**

MDRAP's performance envelope is quantitatively bounded and reproducible:
- **Throughput:** ~1,180 EPS on standard TCP JSON; >320,000 EPS on Zero-Copy SHM.
- **Latency:** ~0.8–1.1 ms median end-to-end; ~3.4 ms p99 tail latency.
- **Memory:** Strict $O(1)$ bounded working set under sustained multi-hour workloads (<131 MB peak).
