# MDRAP Phase 6 — Empirical Workload Model & Projection Analysis

## 1. Executive Summary & Purpose
A credible scaling architecture must be founded on a quantitative workload model that maps out data volumes, ingress rates, burst dynamics, and fan-out pressure across measured current baselines and future expansion tiers. This document defines the mathematical workload model for Phase 6.

---

## 2. Quantitative Workload Matrix

| Workload Dimension | Current Baseline (Phase 5 Pilot) | Expansion Tier 1 (Target Phase 6) | Expansion Tier 2 (Long-Term Horizon) |
| :--- | :--- | :--- | :--- |
| **Active Feeds** | 2 feeds (ITCH + BATS Replay) | 5 feeds (Direct US Lit Exchanges) | 12 feeds (Lit + Dark + Derivatives) |
| **Instrument Universe** | 20 active liquid tickers | 500 symbols (S&P 500 Equities) | 8,000 Equities + 50,000 Options |
| **Sustained Ingest Rate**| 3,166 events / sec (measured) | 10,000 events / sec | 25,000 events / sec |
| **Peak Burst Rate** | 10,000 eps (synthetic burst) | 25,000 eps (180s open surge) | 100,000 eps (30s flash surge) |
| **Event Size Distribution**| SBE: 64B \| Raw: 256B \| DB: 192B | Same wire & storage encoding | Same wire & storage encoding |
| **Active Consumers** | 2 consumers (1 SBE, 1 SHM) | 10 concurrent desk consumers | 25+ enterprise consumer streams |
| **Consumer Fan-Out Bandwidth**| ~400 KB / sec aggregate | ~6.4 MB / sec aggregate | ~40.0 MB / sec aggregate |
| **Daily Stored Volume (8h)**| ~23.3 GB / trading session | ~73.7 GB / trading session | ~184.3 GB / trading session |
| **Consumer Read Patterns** | 100% Streaming SBE | 90% Streaming SBE, 10% Historical | 85% Streaming SBE, 15% Replay/TCA |

---

## 3. Bandwidth and I/O Derivations

### 3.1 Network Ingress & Egress Bandwidth
- **Ingress Bandwidth at 10,000 eps**:
  $$\text{BW}_{\text{ingress}} = 10,000\text{ eps} \times 256\text{ bytes} = 2.56\text{ MB/sec} \approx 20.48\text{ Mbps}$$
- **Consumer Fan-Out Egress (10 Consumers at 10,000 eps)**:
  $$\text{BW}_{\text{egress}} = 10 \times (10,000\text{ eps} \times 64\text{ bytes}) = 6.40\text{ MB/sec} \approx 51.2\text{ Mbps}$$
- **Conclusion**: Network interface bandwidth is well within standard 1 Gbps / 10 Gbps Ethernet limits; network transmission alone is not the primary bottleneck.

### 3.2 Persistent Storage Write Pressure
- **IngestLog WAL Sequential Writes**:
  $$\text{Disk Throughput}_{\text{WAL}} = 10,000\text{ eps} \times 256\text{ bytes} \approx 2.56\text{ MB/sec}$$
  With grouped commits (`fsync_policy="grouped_by_size"`), writes are executed in 64 KB blocks:
  $$\text{Syscall Rate} = \frac{2,560,000\text{ B/sec}}{65,536\text{ B/write}} \approx 39\text{ writes/sec}$$
- **SQLite Canonical Writes (WAL Mode)**:
  $$\text{Disk Throughput}_{\text{DB}} = 10,000\text{ eps} \times 192\text{ bytes} \approx 1.92\text{ MB/sec}$$
  Batched `executemany` (1,000 events/commit) requires 10 SQLite commits/sec.
- **Conclusion**: Storage bandwidth is easily handled by modern NVMe SSDs, provided single-event synchronous `fsync()` is avoided.
