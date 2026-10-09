# MDRAP Phase 5 — Capacity Management and Sizing Architecture

## 1. Executive Summary & Sizing Strategy
This document details the hardware, operating system, and storage capacity model for running the Market Data Reliability & Acceleration Platform (MDRAP) under **Profile A (Single-Node High Throughput)** and future multi-node distributed tiers. MDRAP utilizes bounded-memory data structures, fixed-size pre-allocated shared memory (SHM) ring buffers, and batched sequential I/O to ensure linear, predictable scaling without catastrophic GC pauses or out-of-memory crashes.

---

## 2. Resource Consumption Model & Workload Calculations

### 2.1 Event Size and Data Density
Under standard equity and derivative tick feeds:

| Data Layer | Avg. Event Size (bytes) | Storage Format | Compression / Encoding |
| :--- | :--- | :--- | :--- |
| **Raw Event (Ingress)** | ~256 bytes | IngestLog WAL (`.seg`) | Uncompressed binary record with CRC32 |
| **Canonical Event** | ~180 bytes | SQLite WAL (`canonical.db`)| B-Tree table, batched `executemany` |
| **SBE Distribution Frame** | ~64 bytes | TCP / SHM ring buffer | Binary Simple Binary Encoding (SBE) |
| **Quarantine Record** | ~320 bytes | SQLite (`quarantine.db`) | Raw JSON + 64-bit reason bitmask |

### 2.2 Throughput to Storage Bandwidth Derivations
Assuming standard market hours (8.0 hours / trading session):

| Sustained Ingest Rate | Hourly Data Volume | Daily Session Volume (8 hrs) | Monthly Volume (22 trading days) | Min. Write IOPS |
| :--- | :--- | :--- | :--- | :--- |
| **1,000 eps** | 0.92 GB / hr | 7.36 GB / day | 161.9 GB / month | 200 IOPS |
| **3,166 eps (Pilot Baseline)** | 2.92 GB / hr | 23.36 GB / day | 513.9 GB / month | 600 IOPS |
| **10,000 eps (Profile A Peak)**| 9.22 GB / hr | 73.76 GB / day | 1.62 TB / month | 1,800 IOPS |
| **50,000 eps (Stress Surge)** | 46.1 GB / hr | 368.8 GB / day | 8.11 TB / month | 8,500 IOPS |

*Note*: Grouped fsync batching (`fsync_policy="grouped_by_size"`) consolidates thousands of events into single 64 KB kernel write syscalls, reducing physical disk IOPS requirements by a factor of 40x.

---

## 3. Host Sizing Matrix (Profile A Specification)

To sustain the 10,000 eps peak SLA with sub-millisecond tail latency, the host node must meet or exceed the following specifications:

| Hardware Component | Baseline (Pilot Minimal) | Target Production (Profile A) | High-Surge Institutional Tier |
| :--- | :--- | :--- | :--- |
| **CPU Architecture** | x86-64 / AMD64 (4 Cores) | x86-64 (8 Physical Cores, pinned) | AMD EPYC / Intel Xeon (16 Cores) |
| **Clock Frequency** | >= 2.8 GHz | >= 3.6 GHz base | >= 4.0 GHz all-core turbo |
| **System RAM** | 8 GB DDR4 | 16 GB DDR4/DDR5 ECC | 32 GB DDR5 ECC |
| **Storage (WAL / Active)** | 250 GB NVMe PCIe Gen3 | 500 GB NVMe PCIe Gen4 (Direct) | 1 TB Enterprise NVMe (U.2/U.3) |
| **Storage IOPS** | 10,000 IOPS read/write | 50,000 IOPS sequential write | 200,000 IOPS sustained write |
| **Network Interface** | 1 Gbps Ethernet | 10 Gbps SFP+ (Dedicated VLAN) | 25 Gbps RoCE / Solarflare Onload |

---

## 4. CPU Pinning and Concurrency Topology

To prevent kernel context switches, CPU migration, and thread scheduling contention, MDRAP assigns specific core affinities on multi-core hosts:

```
[ Core 0 ] ──> OS Kernel / IRQ Handling / Network Stack
[ Core 1 ] ──> Feed Ingress Worker (TCP / UDP Socket Listener)
[ Core 2 ] ──> Normalization & Quality Rule Engine (Fastpath C)
[ Core 3 ] ──> Cross-Feed Reconciler & Canonical Ordering
[ Core 4 ] ──> IngestLog Disk Writer & WAL Checkpointer
[ Core 5 ] ──> SBE Distribution Engine (SHM Writer / TCP Sockets)
[ Core 6-7] ──> API Server (FastAPI / Prometheus Metrics / Ops Tooling)
```

---

## 5. Memory Allocation & Bounded Boundaries

MDRAP enforces strictly bounded memory allocations across all internal buffers:

1. **Shared Memory Ring Buffer**: Fixed at 64 MB (`--shm-size 67108864`), pre-allocated at startup via OS `shm_open` or Windows Named File Mapping.
2. **IngestLog Ring Queue**: Bounded to 100,000 events (`~25 MB`). If queue reaches 80% capacity, backpressure rate limiting engages.
3. **SQLite Page Cache**: Configured via `PRAGMA cache_size = -65536;` (allocating exactly 64 MB for in-memory page caching per database file).
4. **Welford Rolling Window**: Price anomaly detection maintains rolling statistical moments in $O(1)$ space (3 floats: count, mean, M2) per instrument.
5. **Total Resident Memory Footprint (RSS)**: Under peak 10,000 eps load, steady-state RSS is bounded to **< 512 MB**.
