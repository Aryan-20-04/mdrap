# MDRAP Phase 5 — Operational Cost Assumptions and Modeling Baseline

## 1. Executive Summary
This document records the operational, financial, and infrastructural assumptions used to calculate total cost of ownership (TCO) and per-event processing economics for MDRAP under **Profile A (Single-Node High Throughput)**.

---

## 2. Market and Workload Assumptions

| Parameter | Assumed Value | Justification / Source |
| :--- | :--- | :--- |
| **Trading Days per Year** | 252 days | Standard US Equity & Derivative exchange calendar. |
| **Active Session Duration** | 8.0 hours (28,800 sec) | Pre-market open (08:30 EST) through post-market close (16:30 EST). |
| **Average Ingest Throughput** | 3,166 events / second | Measured empirical baseline from Phase 5 pilot benchmark. |
| **Peak Burst Throughput** | 10,000 events / second | Profile A SLA specification. |
| **Average Raw Event Size** | 256 bytes | Standard SBE/ITCH normalized binary frame size. |
| **Average Canonical Event Size**| 192 bytes | SQLite serialized row with 64-bit indexes. |
| **Zstandard Compression Ratio**| 5.81 : 1 (82.8% reduction) | Empirical compression benchmark on 25k event sample. |

---

## 3. Infrastructure & Hosting Pricing Assumptions

### 3.1 Colocation (Equinix NY4 / NJ2)
- **Rack Space**: \$1,200/month per 2U cabinet space, including redundant A/B 208V power feeds and cooling.
- **Physical Server Hardware**: \$8,500 CAPEX for 1U Enterprise Server (8 Cores, 64 GB DDR5 ECC, 2x 1TB Enterprise NVMe SSDs). Depreciated straight-line over 36 months (\$236.11/mo).
- **Physical Cross-Connect**: \$1,500/month standard Equinix direct fiber cross-connect to exchange meet-me room.
- **Out-of-Band Console**: \$150/month dedicated terminal server VPN port.

### 3.2 Cloud Infrastructure (AWS us-east-1)
- **EC2 Instance Type**: `c6i.2xlarge` compute-optimized instance at \$0.34/hr on-demand or \$0.208/hr under 1-year Savings Plan (\$150–\$248/mo).
- **EBS Storage**: 500 GB `gp3` provisioned volume at \$0.08/GB-month (\$40.00/mo).
- **S3 Object Storage**: S3 Standard storage tier at \$0.023/GB-month for cold archive.
- **Data Transfer**: Internal AWS VPC peering transfer at \$0.01/GB; inter-AZ egress at \$0.02/GB.

---

## 4. Human Resources & Staffing Model
- **Platform Engineering (SRE)**: Profile A does not require dedicated full-time headcount. It is supported by an existing centralized SRE rotation.
- **Allocation**: **0.2 FTE** (Full-Time Equivalent) dedicated platform engineering overhead for routine upgrades, certificate rotation, and monitoring maintenance (~1 day/week).
- **Audit & Compliance Overhead**: 4 hours/month for EOD accounting reconciliation verification and exchange manifest delivery.
