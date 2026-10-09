# MDRAP Phase 5 — Operational Cost Model and Total Cost of Ownership (TCO)

## 1. Executive Summary & Cost Philosophy
MDRAP's architectural design—relying on pure Python orchestration, native C hot path acceleration, zero heavyweight ORM frameworks, and lightweight SQLite WAL persistence—delivers an exceptionally low infrastructure cost footprint compared to traditional commercial market data appliances (which often require multi-million dollar annual vendor licensing and specialized proprietary appliances).

This document details the complete Total Cost of Ownership (TCO) model for operating MDRAP under **Profile A (Single-Node High Throughput)** across colocation and cloud environments.

---

## 2. Infrastructure Cost Breakdown (Monthly & Annualized)

### 2.1 Colocation / Bare-Metal Deployment (Equinix NY4 / Carteret NJ)

| Cost Component | Specification | Monthly Cost (USD) | Annual Cost (USD) |
| :--- | :--- | :--- | :--- |
| **Colo Rack Space & Power** | 2U Rack Unit (1.5 kW redundant power) | \$1,200 | \$14,400 |
| **Hardware Server Amortization**| 1U Dell R660 (8-Core Intel, 64GB, 2x1TB NVMe, \$8,500 amortized over 36 mo) | \$236 | \$2,833 |
| **Dedicated Network Cross-Connect**| 10 Gbps SFP+ to Exchange Demarc | \$1,500 | \$18,000 |
| **Out-of-Band Management (OOB)**| 100 Mbps Terminal Console & VPN | \$150 | \$1,800 |
| **Cold Storage Backup (S3 / NAS)**| 2 TB Monthly Incremental Cold Archive | \$46 | \$552 |
| **Subtotal Infrastructure** | | **\$3,132 / mo** | **\$37,585 / yr** |

---

### 2.2 Cloud / Virtualized Deployment (AWS us-east-1)

| Cost Component | Specification | Monthly Cost (USD) | Annual Cost (USD) |
| :--- | :--- | :--- | :--- |
| **Compute Instance** | `c6i.2xlarge` (8 vCPU, 16 GB RAM, Dedicated) | \$248 | \$2,976 |
| **Primary Storage (EBS)** | 500 GB `gp3` (3,000 IOPS, 250 MB/s) | \$40 | \$480 |
| **Cold Archival Storage** | AWS S3 Standard (1 TB rolling archive) | \$23 | \$276 |
| **Network Egress Bandwidth** | 500 GB / month to consumer VPCs | \$45 | \$540 |
| **Telemetry & CloudWatch** | Custom Prometheus metrics / alarms | \$35 | \$420 |
| **Subtotal Cloud Infrastructure** | | **\$391 / mo** | **\$4,692 / yr** |

*Note*: Cloud deployments avoid physical cross-connect fees but introduce network jitter and higher tail latency (p99 ~ 850 µs vs ~ 280 µs bare metal).

---

## 3. Unit Economics: Cost per Million Events Processed

Assuming a standard trading desk workload running Profile A:
- Average Ingest Rate: `3,166 events / second`
- Daily Event Volume: $3,166 \times 28,800\text{ sec} = 91,180,800\text{ events / day}$
- Monthly Event Volume (22 trading days): $2,005,977,600\text{ events / month}$ (~2.0 Billion events)

### Unit Cost Calculations:

$$\text{Unit Cost (Cloud)} = \frac{\$391}{2,005.98\text{ Million Events}} \approx \mathbf{\$0.195\text{ per Million Events}}$$

$$\text{Unit Cost (Colo + Cross-Connect)} = \frac{\$3,132}{2,005.98\text{ Million Events}} \approx \mathbf{\$1.56\text{ per Million Events}}$$

---

## 4. Software Licensing and Overhead Efficiency
- **Core Engine Software Licensing**: **\$0.00** (Proprietary internally developed open/in-house core; zero recurring vendor seat fees).
- **External Dependencies**: Zero commercial database licenses (SQLite and native C stdlib are free from external royalty obligations).
- **Efficiency Metric**: MDRAP processes over 2 billion validated, normalized, and metered market events per month for under \$400 in direct cloud infrastructure.
