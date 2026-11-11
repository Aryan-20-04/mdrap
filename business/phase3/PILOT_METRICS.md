# MDRAP Phase 3 — Pilot Success Metrics & Measurement Framework

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Lead Product Analytics & Metrics Engineer (Agent 6)  
**Status**: APPROVED  

---

## 1. Measurement Philosophy

Success during a customer pilot cannot be measured solely by vanity metrics (e.g. "millions of events processed"). Success must be measured by **demonstrable reduction in operational downtime, engineering hours saved, and precision detection of anomalies that existing tools failed to catch**.

Every metric is tracked against a **pre-pilot baseline** established during Day 1–3 of the deployment.

---

## 2. Technical Quality & System Performance Metrics

| Metric Dimension | Specific Metric | Measurement Method | Target Baseline (Customer Status Quo) | Target Pilot SLA |
| :--- | :--- | :--- | :--- | :--- |
| **Feed Freshness & Staleness** | Stale feed detection latency | Time delta from actual stream cessation to `SILENT` alert. | 30 – 120 seconds (manual ping / trader alert) | **$< 2.0\text{ seconds}$** |
| **Sequence Integrity** | Sequence-gap detection rate | Count of sequence discontinuities detected via bitmap. | Uncounted / silent packet loss | **100% of non-monotonic jumps flagged** |
| **Duplicate Quarantine** | Duplicate identification accuracy | Count of identical sequence/timestamp hashes isolated. | Frequently re-processed or causes book corruption | **100% quarantine; zero state distortion** |
| **Incident Detection Latency** | Anomaly to incident creation | Timestamp delta from receipt of corrupt tick to incident creation. | Hours (detected post-trade by trader) | **$< 500\ \mu\text{s}$ inline processing** |
| **Replay Reproducibility** | Deterministic state digest match | Hash comparison of repeated replays across application restarts. | 0% (inability to recreate historical in-memory state) | **100.0% bit-for-bit identical digest** |
| **Provenance Completeness** | % of events with SHA-256 + Lineage | Ratio of SQLite provenance records to incoming raw events. | 0% (unindexed text logs) | **100.0% (Zero unpersisted events)** |
| **Resource Overhead** | Additional per-event latency | End-to-end benchmark timing (`perf_counter`). | N/A | **$< 50.0\ \mu\text{s}$ per event** |
| **Process Memory Footprint** | Resident Set Size (RSS) growth | OS memory tracking over 24-hour continuous run. | N/A | **$< 150\text{ MB}$ steady-state RSS** |
| **Host Disk Growth** | Storage consumption rate | Daily SQLite WAL growth per 1,000 events/sec. | Uncontrolled text logs (GBs/day) | **$< 16\text{ GB/day}$ at 1,000 eps** |

---

## 3. Business & Operational Value Metrics

| Value Dimension | Specific Metric | Customer Baseline (Pre-MDRAP) | Expected Pilot Outcome (With MDRAP) | Value Delivered |
| :--- | :--- | :--- | :--- | :--- |
| **Post-Mortem Investigation Time** | Hours to conclude root cause of a market data anomaly | **8 to 24 engineering hours** (spread over 2–4 days) | **$< 15\text{ minutes}$** via `mdrap replay` & `incident list` | **95%+ reduction in investigation time** |
| **Dispute Resolution Speed** | Time to export verifiable evidence for exchange support | **2 to 5 business days** of manual CSV/Wireshark aggregation | **$< 60\text{ seconds}$** via `tools/verify_evidence.py` | **Instant, mathematically defensible proof** |
| **Developer Productivity** | Senior quant dev hours spent on feed handler schema bugs | **10 – 20 hours/month** per engineer | **$< 2\text{ hours/month}$** | **Frees up senior engineers for alpha research** |
| **Bad-Tick PnL Prevention** | Avoided adverse selection fills from crossed or stale books | 1 – 3 bad fills per quarter (\$10k – \$50k loss) | Identified in real time via `SUSPICIOUS` status | **Prevents five-figure slippage losses** |
| **Deployment & Onboarding Effort** | Hours required for customer to install and verify sidecar | Days of complex integration | **$< 45\text{ minutes}$** turnkey setup | **Frictionless proof of value** |

---

## 4. Anomaly Classification & Ground-Truth Validation

To avoid false alarms and preserve trust:
- **True Positive**: MDRAP flags an event as `SUSPICIOUS` or `INVALID` and customer postmortem confirms actual exchange packet loss, stale feed, or crossed book.
- **False Positive (Target: $< 0.1\%$)**: MDRAP flags an event as bad when exchange order book was genuinely orderly. (Monitored and tuned via configurable thresholds in `config.json`).
- **False Negative (Target: $0.0\%$)**: A crossed book or sequence drop reaches customer trading models without an MDRAP incident being raised.
