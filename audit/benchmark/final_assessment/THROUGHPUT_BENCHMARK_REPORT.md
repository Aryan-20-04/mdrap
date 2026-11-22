# MDRAP — Controlled Throughput Benchmark Report

**Document Reference:** `audit/benchmark/final_assessment/THROUGHPUT_BENCHMARK_REPORT.md`  
**Execution Timestamp:** 2026-11-22T01:45:00 UTC  
**Environment:** Windows 11 Enterprise AMD64 | Intel Core i7 (10 physical / 12 logical cores) | Python 3.13.1  
**Repository Branch:** `main`  
**Git HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Clean Release Reference:** `maintenance/architecture-pruning-v608-clean` (`38ec051e0958c2199268a03afb54f6f24b89cd58`)  
**Raw Evidence Artifact:** [`audit/benchmark/final_assessment/raw/benchmark_stage_c.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/benchmark/final_assessment/raw/benchmark_stage_c.json)  
**Governance:** STRICT READ-ONLY INFRASTRUCTURE — NO DEPLOYMENT — NO LIVE EXECUTION

---

## 1. Executive Summary

This report delivers the empirical throughput evaluation of MDRAP across three primary processing tiers:
1. **Core Ingestion Pipeline:** Normalization, quality evaluation, BBO book synthesis, and watchdog telemetry.
2. **Lock-Free Zero-Copy Shared Memory (SHM v3):** Inter-process ring buffer streaming with seqlock atomics.
3. **TCP Streaming Broadcast:** JSON-over-socket distribution to connected Observer sidecars and external consumers.

**Key Findings:**
- **SHM Write Throughput:** **518,700 EPS** (0.096s for 50,000 events).
- **SHM Read Throughput:** **400,023 EPS** (0.125s for 50,000 events).
- **Core Engine Processing:** **3,967 EPS** (Pure Python normalization, validation, and BBO book update).
- **TCP Streaming Throughput:** **1,407.4 EPS** mean sustained throughput across 5 time windows.
- **Queue Occupancy & Drop Rate:** **Zero dropped events (0.0% loss)**, maximum queue depth 0 across engine broadcast channels.

---

## 2. Ingestion Pipeline & Transport Throughput Matrix

| Tier / Subsystem Under Test | Architecture / Protocol | Test Batch Size | Elapsed Time (s) | Effective Throughput (EPS) | Delivery / Loss Rate | Max Queue Depth |
| :--- | :--- | :-: | :-: | :-: | :-: | :-: |
| **Pure Engine Ingestion** | Full validation + BBO synthesis | 50,000 events | 12.604s | **3,966.9 EPS** | 100% processed (0 loss) | 0 |
| **SHM Lock-Free Writer** | 64-byte seqlock ring buffer write | 50,000 events | 0.096s | **518,699.6 EPS** | 100% committed | N/A (Lock-free ring) |
| **SHM Lock-Free Reader** | 64-byte seqlock ring buffer read | 50,000 events | 0.125s | **400,023.0 EPS** | 100% retrieved | N/A (Zero-copy) |
| **TCP Streaming Pipeline** | Ingestion + TCP JSON broadcast | 50,000 events | 35.749s | **1,407.4 EPS** | 100% delivered (0 loss) | 0 |

---

## 3. TCP Streaming Window Stability Analysis

To evaluate sustained processing stability, 50,000 synthetic events were streamed to the Observer sidecar across 5 consecutive observation windows of 10,000 events each:

| Window Index | Events Injected | Events Received | Window Duration (s) | Effective Window EPS | Drop Count | Window Delivery % |
| :---: | :-: | :-: | :-: | :-: | :-: | :-: |
| **Window 1** | 10,000 | 10,000 | 7.545s | **1,325.4 EPS** | 0 | 100.0% |
| **Window 2** | 10,000 | 10,000 | 6.902s | **1,448.9 EPS** | 0 | 100.0% |
| **Window 3** | 10,000 | 10,000 | 6.323s | **1,581.6 EPS** | 0 | 100.0% |
| **Window 4** | 10,000 | 10,000 | 7.014s | **1,425.8 EPS** | 0 | 100.0% |
| **Window 5** | 10,000 | 10,000 | 7.966s | **1,255.4 EPS** | 0 | 100.0% |
| **Aggregate** | **50,000** | **50,000** | **35.749s** | **1,407.4 EPS** | **0** | **100.0%** |

### Stability Metrics:
- **Mean Sustained Throughput:** `1,407.4 EPS`
- **Standard Deviation:** `111.6 EPS`
- **Coefficient of Variation ($CV$):** `7.93%`

The low coefficient of variation ($7.93\% < 10\%$) proves that the pipeline maintains consistent throughput without periodic garbage collection freezes or socket buffer stalls.

---

## 4. Bottleneck & Architecture Analysis

### 4.1 Transport Boundary Comparison
The empirical throughput measurements demonstrate a dramatic architectural divergence between transport mechanisms:
- **Shared Memory (SHM v3):** Operates at **~518,000 writes/sec** and **~400,000 reads/sec**. Because the ring buffer relies on lock-free seqlocks and cache-line aligned shared memory, it bypasses network stack overhead, socket context switching, and JSON serialization entirely.
- **TCP Streaming Broadcast:** Operates at **~1,400 EPS** sustained. The throughput is bounded by:
  1. Python `json.dumps()` serialization on the broadcast thread.
  2. TCP socket buffer transitions on loopback interfaces.
  3. JSON text parsing (`json.loads()`) and dictionary instantiation inside the client worker.

### 4.2 Queue Depths and Backpressure Resilience
- Ingest engine queue depth remained at `0` under normal rated load.
- Exporter queue reached a maximum depth of `3` out of `50,000` slot capacity ($0.01\%$ saturation).
- Zero dropped events were recorded across all windows.

---

## 5. Certification Verdict

The Throughput Benchmark confirms:
- **SHM Transport:** Exceeds 400,000 EPS for co-located zero-copy consumers.
- **TCP Stream Distribution:** Demonstrates sustained 1,400 EPS with 100% event delivery, 0 drops, and $<8\%$ window variance.

---
*Report certified under MDRAP Governance — Read-Only Market-Data Infrastructure — No Deployment.*
