# MDRAP — Stage C1: Short Benchmark Smoke Test Report

**Document Reference:** `audit/benchmark/final_assessment/stage_c/STAGE_C1_SMOKE_TEST_REPORT.md`  
**Execution Timestamp:** 2026-11-22T07:42:48Z (2026-11-22T13:12:48+05:30 Local)  
**Host Environment:** Microsoft Windows 11 Enterprise AMD64 (10 physical cores, 12 logical cores) | Python 3.13.1  
**Git Branch:** `main` | **HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Raw Artifact:** [`audit/benchmark/final_assessment/stage_c/raw/smoke_c1.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/benchmark/final_assessment/stage_c/raw/smoke_c1.json)  
**Governance:** STRICT READ-ONLY INFRASTRUCTURE — NO DEPLOYMENT

---

## 1. Executive Summary

Stage C1 executed a short, deterministic synthetic workload of **10,000 events** paced at **1,000 events/second** across the complete end-to-end platform pipeline:
`Synthetic Generator -> MarketDataDaemon (TCP) -> ObserverClient -> ObserverState -> ObserverExporter (JSONL)`.

The primary objectives were to verify event generation fidelity, exact producer/consumer accounting reconciliation, sequence continuity, multi-stage monotonic latency tracking, JSONL export integrity, and clean resource teardown prior to running higher-intensity benchmarks.

### Key Results Summary

| Metric | Target / Expected | Observed / Measured | Status |
| :--- | :---: | :---: | :---: |
| **Total Events Sent** | 10,000 | **10,000** | **VERIFIED** |
| **Total Events Received** | 10,000 | **10,000** | **VERIFIED** |
| **Delivery Ratio** | 100.0% | **100.00%** | **VERIFIED** |
| **Dropped Events** | 0 | **0** | **VERIFIED** |
| **Checksum Reconciliation (XOR)** | Exact match | `10000` == `10000` | **VERIFIED** |
| **Checksum Reconciliation (Sum)** | Exact match | `50,005,000` == `50,005,000` | **VERIFIED** |
| **Sequence Gaps / Duplicates** | 0 / 0 | **0 / 0** (Strictly monotonic) | **VERIFIED** |
| **Telescoping Latency Identity** | 100.0% satisfied | **100.00%** (0 discrepancies / 10,000) | **VERIFIED** |
| **Invalid Latency Counts** | 0 | **0** across all 4 stages | **VERIFIED** |
| **Exporter JSONL Exported Lines** | 10,000 | **10,000** (0 parse errors) | **VERIFIED** |
| **Max Exporter Queue Depth** | < 20,000 | **4** (0 drops) | **VERIFIED** |
| **Teardown Cleanliness** | 100% terminated | **Daemon, Client, Exporter Cleanly Exited** | **VERIFIED** |

---

## 2. Event Accounting & Checksum Reconciliation

Event identity tracking verified that zero events were lost, reordered, or synthesized:

- **Producer Sent:** 10,000 events (Duration: 10.002s, Rate: 999.8 EPS)
- **Consumer Received:** 10,000 events (Delivery: 100.00%)
- **Producer ID Range:** `[1, 10000]`
- **Consumer ID Range:** `[1, 10000]`
- **XOR Checksum Verification:**
  $$\bigoplus_{i=1}^{10000} \text{Seq}_i^{\text{prod}} = 10000 = \bigoplus_{i=1}^{10000} \text{Seq}_i^{\text{cons}} \quad (\Delta = 0)$$
- **Arithmetic Sum Verification:**
  $$\sum_{i=1}^{10000} \text{Seq}_i^{\text{prod}} = 50,005,000 = \sum_{i=1}^{10000} \text{Seq}_i^{\text{cons}} \quad (\Delta = 0)$$
- **Sequence Continuity:** Strictly in-order. Sequence gaps: **0**, duplicates: **0**, out-of-order packets: **0**.

---

## 3. Monotonic Multi-Stage Latency & Telescoping Identity

Latency was measured across all four system boundaries using `MonotonicLatencyTracker` backed by `time.perf_counter_ns()`.

### Stage Breakdown Table (in microseconds, $\mu$s)

| Pipeline Stage | Sample Count | Invalid Count | Mean ($\mu$s) | Min ($\mu$s) | p50 ($\mu$s) | p90 ($\mu$s) | p95 ($\mu$s) | p99 ($\mu$s) | p99.9 ($\mu$s) | Max ($\mu$s) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **Ingress** | 10,000 | 0 | 12.38 | 4.3 | 10.8 | 16.3 | 19.7 | 37.2 | 147.3 | 2,011.4 |
| **Engine** | 10,000 | 0 | 668.42 | 130.9 | 596.2 | 986.8 | 1,186.4 | 1,895.4 | 3,750.1 | 110,301.0 |
| **Transport (TCP)** | 10,000 | 0 | 578.03 | 135.7 | 504.4 | 849.9 | 1,066.5 | 1,935.1 | 3,899.6 | 17,323.2 |
| **End-to-End** | 10,000 | 0 | 1,258.83 | 303.0 | 1,136.2 | 1,788.3 | 2,181.8 | 3,363.9 | 7,720.1 | 111,052.2 |

### Telescoping Additive Identity Verification
For every valid observation $i$, the additive identity was evaluated:
$$|(\text{Latency}_{\text{ingress}} + \text{Latency}_{\text{engine}} + \text{Latency}_{\text{transport}}) - \text{Latency}_{\text{e2e}}| \le 0.05\,\mu\text{s}$$
- **Total Evaluated:** 10,000 events
- **Discrepancies:** **0**
- **Identity Compliance:** **100.00%**
- **Invalid Timestamp Accounts:** **0** (No non-monotonic inversions or negative durations).

---

## 4. Exporter Output & JSONL Integrity

The observer background exporter was verified for file persistence and schema validity:
- **Files Created:** 2 JSONL files (`events_1795333369.jsonl`, `health_1795333369.jsonl`)
- **Total Exported Lines:** 10,000
- **Line Count Match:** Exact (10,000 exported lines == 10,000 consumer events)
- **JSON Parse Errors:** **0** (100% valid JSON objects)
- **Queue Saturation:** Peak queue depth observed was **4 items** out of a 20,000 bounded capacity (0.02% saturation). Zero dropped records.
- **SHA-256 File Manifest:**
  - `events_1795333369.jsonl`: `60f531fc6c2a2a01114de026115cb69a100ab5a026657a1bfaaf16df19f2b699`
  - `health_1795333369.jsonl`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

---

## 5. Resource Consumption & Teardown

- **Baseline Working Set (RSS):** 38.14 MB
- **Initialized Pipeline RSS:** 47.94 MB
- **Peak RSS Under Load:** 54.89 MB
- **Steady-State RSS:** 54.89 MB
- **Post-Teardown RSS:** 49.68 MB (after `gc.collect()`)
- **CPU Time:** 5.734s User, 5.156s System (Total: 10.891s across 10.052s elapsed -> 108.3% CPU utilization across logical cores).
- **Teardown State:**
  - `ObserverClient`: Stopped (`client.running == False`)
  - `ObserverExporter`: Drained and stopped (`exporter.running == False`)
  - `MarketDataDaemon`: Stopped (`daemon._running == False`)
  - Temporary files unlinked cleanly. Zero orphan threads or leaked socket ports.

---

## 6. Smoke Test Verdict

**VERDICT: VERIFIED**

The Stage C1 smoke test confirmed full operational readiness. All event generation, sequence continuity, monotonic latency measurement, JSONL export, and resource teardown mechanisms operate correctly without data loss, sequence corruption, or instrumentation defects.

Proceeding to **Stage C2 (Throughput Benchmark)**, **Stage C3 (Latency Benchmark)**, and **Stage C4 (Resource Benchmark)**.
