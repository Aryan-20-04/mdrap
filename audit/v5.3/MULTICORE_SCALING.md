# MDRAP v5.3 — Multicore Scalability & Pipeline Boundary Report

## Executive Summary
This document resolves the architectural boundary conflation identified in MDRAP v5.2, where 97.1k EPS (reported across 4 workers) was mistakenly compared against 26.4k EPS (measured on a single worker).

---

## 1. Boundary Clarification & Workload Definitions

In MDRAP, two completely distinct workloads exist:

1. **Stateless Ingress Workload (Boundary B + C)**:
   - Operation: Ingest raw JSON payload $\to$ schema validation & normalization (`normalize(raw)`) $\to$ native quality evaluation (`FastQualityEngine.evaluate()`).
   - Characteristics: Purely CPU-bound, stateless, zero disk I/O, independent across symbol channels.
   - Ideal Scaling: Near-linear scaling across multiple CPU cores.

2. **Stateful Transactional Pipeline Workload (Boundary H)**:
   - Operation: Ingest raw JSON $\to$ schema normalization $\to$ `engine.step()` with state machine transitions, undo journaling, sequence bitmap maintenance, BBO synthesis $\to$ SHM seqlock publishing.
   - Characteristics: Stateful, serial dependency on symbol-venue sequence, transaction journal allocation, memory barriers.

---

## 2. Measured Multicore Scaling Results (v5.3 Verified)

Measured on AMD Ryzen 7 5800H (8 Cores / 16 Threads, 3.20 GHz, Windows 11) and recorded in `audit/v5.3/raw/multicore_scaling.json`:

| Worker Count | Stateless Ingress (EPS) | Speedup (Stateless) | Per-Worker (Stateless) | Stateful Pipeline (EPS) | Speedup (Stateful) | Per-Worker (Stateful) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1 Worker** | 58,846.3 | 1.00x (Baseline) | 58,846.3 | 23,672.6 | 1.00x (Baseline) | 23,672.6 |
| **2 Workers** | 82,510.9 | 1.40x | 41,255.4 | 31,408.8 | 1.33x | 15,704.4 |
| **4 Workers** | 102,080.2 | 1.73x | 25,520.1 | 54,676.8 | 2.31x | 13,669.2 |
| **8 Workers** | 105,814.9 | 1.80x | 13,226.9 | 82,055.5 | 3.47x | 10,256.9 |

---

## 3. Analysis & Key Insights

1. **Resolution of the 97.1k vs 26.4k Mystery**:
   - The 97,171.1 EPS reported in v5.2 was measured using the **Stateless Ingress** workload on 4 workers.
   - The 26,424.8 EPS reported in v5.2 was measured using the **Stateful Pipeline** workload on 1 worker.
   - In v5.3, under exact boundary labeling:
     - 4-worker Stateless Ingress achieves **102,080.2 EPS** (completely validating the ~97k-102k range).
     - 1-worker Stateful Pipeline achieves **23,672.6 EPS** (completely validating the ~23k-26k range).
     - 8-worker Stateful Pipeline scales up to **82,055.5 EPS** (3.47x scaling).
2. **Diminishing Returns on Stateless Scaling**:
   Stateless ingress tops out around 105k EPS due to Python multiprocessing IPC pickling overhead when dispatching raw events across worker pools.
3. **Partitioning by Instrument/Venue**:
   To achieve optimal institutional scaling, workers should be pinned to dedicated exchange feed sockets with zero cross-process synchronization (shared-nothing architecture), routing normalized events directly to symbol-sharded SHM rings.
