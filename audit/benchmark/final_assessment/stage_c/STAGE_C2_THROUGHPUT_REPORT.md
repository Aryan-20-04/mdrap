# MDRAP — Stage C2: Throughput Benchmark Report

**Document Reference:** `audit/benchmark/final_assessment/stage_c/STAGE_C2_THROUGHPUT_REPORT.md`  
**Execution Timestamp:** 2026-11-22T07:44:48Z (2026-11-22T13:14:48+05:30 Local)  
**Host Environment:** Windows 11 Enterprise AMD64 (10 physical cores, 12 logical cores, 15.72 GB RAM) | Python 3.13.1  
**Git Branch:** `main` | **HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Raw Evidence Artifact:** [`audit/benchmark/final_assessment/stage_c/raw/stage_c_benchmarks.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/benchmark/final_assessment/stage_c/raw/stage_c_benchmarks.json)  
**Governance:** STRICT READ-ONLY INFRASTRUCTURE — NO DEPLOYMENT

---

## 1. Executive Summary

Stage C2 measured the maximum and sustained throughput of MDRAP across multiple transport and engine boundaries using synthetic, locally generated workloads:
1. **Core Ingestion Engine**: Pure Python event processing, validation, normalization, and internal dispatch.
2. **Zero-Copy Shared Memory (SHM)**: Lock-free SPMC circular ring buffer throughput for both producer writes and consumer reads.
3. **TCP Streaming Pipeline**: Sustained windowed delivery through `MarketDataDaemon` to `ObserverClient`.
4. **Observer Exporter**: Disk-backed asynchronous JSONL persistence throughput.

> [!NOTE]
> **Measurement Distinction**: These throughput measurements reflect synthetic, local loopback and in-memory workloads designed to stress software queues, serialization, and CPU bottlenecks. They must NOT be presented as live exchange-feed throughput, which is constrained by external NICs, WAN latencies, and exchange gateway throttling.

---

## 2. Throughput Results Across System Tiers

| Pipeline Component / Tier | Workload Size | Elapsed Time (s) | Measured Throughput (EPS) | Delivery / Success Ratio |
| :--- | :---: | :---: | :---: | :---: |
| **Pure Engine Ingestion** | 50,000 events | 20.848s | **2,398.3 EPS** | 100.00% |
| **SHM Zero-Copy Write** | 50,000 events | 0.153s | **326,695.5 EPS** | 100.00% |
| **SHM Zero-Copy Read** | 50,000 events | 0.191s | **262,221.8 EPS** | 100.00% |
| **TCP Sustained Pipeline** | 50,000 events | 40.742s | **1,182.0 EPS** (Mean) | 100.00% (0 drops) |
| **Exporter Disk Persistence** | 50,000 events | 0.829s | **60,279.3 EPS** | 100.00% (0 errors) |

---

## 3. Sustained Windowed Throughput & Stability (TCP)

The streaming pipeline was tested across 5 consecutive sustained windows of 10,000 events each (total 50,000 events) delivered over TCP loopback to evaluate inter-window variability, queue occupancy, and backpressure:

| Window Index | Events Sent | Events Received | Window Elapsed (s) | Effective Rate (EPS) | Window Delivery |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **Window 1** | 10,000 | 10,000 | 9.556s | 1,046.4 EPS | 100.0% |
| **Window 2** | 10,000 | 10,000 | 9.022s | 1,108.5 EPS | 100.0% |
| **Window 3** | 10,000 | 10,000 | 8.568s | 1,167.2 EPS | 100.0% |
| **Window 4** | 10,000 | 10,000 | 8.539s | 1,171.1 EPS | 100.0% |
| **Window 5** | 10,000 | 10,000 | 7.057s | 1,417.0 EPS | 100.0% |

### Statistical Distribution:
- **Mean Sustained Throughput:** **1,182.0 EPS**
- **Standard Deviation:** **126.0 EPS**
- **Coefficient of Variation (CV):** **10.66%**
- **Total Dropped Events:** **0**
- **Backpressure / Queue Stalls:** Zero buffer overflows; the consumer processed every event without socket resets or packet drops.

---

## 4. Exporter Disk Persistence Throughput

The asynchronous JSONL exporter (`ObserverExporter`) was evaluated under a burst injection of 50,000 market events:
- **Events Written to Disk:** 50,000 events in 0.8295 seconds -> **60,279.3 EPS**
- **Maximum Queue Occupancy:** 49,876 items / 100,000 capacity (**49.88% peak saturation**)
- **Dropped Records:** **0** (No queue drops occurred)
- **Disk Write Errors:** **0**
- **Flush Integrity:** Background worker cleanly flushed all memory buffers to disk upon stop signal.

---

## 5. Technical Limitations & Observations

1. **TCP Serialization Overhead**: Python stdlib JSON serialization and loopback socket dispatch in `MarketDataDaemon` sustain approximately 1,180–2,400 EPS in pure Python.
2. **SHM Acceleration Potential**: When bypassing socket serialization and leveraging memory-mapped circular ring buffers (`mdrap.shm`), throughput reaches **>320,000 EPS** (a >130x acceleration over TCP).
3. **Queue Headroom**: Under burst conditions, the observer queue absorbed 49,876 records with zero drop, fully flushing within 830ms.

---

## 6. Stage C2 Verdict

**VERDICT: VERIFIED**

The throughput capability of the engine, transport, and persistence tiers is documented with exact reproducible timings. All producer and consumer counts reconcile with zero event drops.
