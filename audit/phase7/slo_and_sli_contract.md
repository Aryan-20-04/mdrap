# MDRAP Phase 7 — Service Level Objectives (SLO) & Service Level Indicators (SLI) Contract

## 1. Executive Summary & Governance Contract
To establish clear, measurable operational expectations between the market data infrastructure platform and institutional trading consumers, this contract defines the formal **Service Level Indicators (SLIs)** and **Service Level Objectives (SLOs)** for MDRAP.

---

## 2. Definitive SLI and SLO Specification

| Objective Name | Service Level Indicator (SLI) Calculation | Measurement Window | Target SLO | Error Budget |
| :--- | :--- | :--- | :--- | :--- |
| **Ingress Availability** | $\frac{\text{Successful Ingest Seconds}}{\text{Total Trading Session Seconds}} \times 100$ | Monthly (Trading Hours) | **99.99%** | 2.6 minutes / month |
| **Data Integrity** | $\frac{\text{Events with Valid CRC32 & Monotonic Seq}}{\text{Total Committed Events}} \times 100$ | Continuous (Per Session) | **100.00%** | **Zero Tolerated Faults** |
| **p99 Processing Latency**| $\text{Time from gateway receipt to fan-out enqueue}$ | Rolling 5-minute window | **$\le 50.0\text{ \mu s}$** | $< 0.1\%$ samples $> 50\text{ \mu s}$ |
| **Persistence Success** | $\frac{\text{Successful IngestLog WAL Appends}}{\text{Attempted Appends}} \times 100$ | Continuous | **100.00%** | **Zero Tolerated Faults** |
| **Crash Recovery Time** | $\text{Time from process restart to healthy readiness}$ | Per Failure Event | **$\le 2.0\text{ seconds}$** | Max 5s under large WAL |
| **Noisy Neighbor Isolation**| $\text{Healthy Consumer Ticks Received without Drop}$ | Continuous | **100.00%** | Zero drops on fast clients |

---

## 3. Error Budget Depletion & Escalation Policies
1. **Tier 1 Alert (Error Budget $> 50\%$ consumed)**: Warning to platform engineering team; non-critical releases paused.
2. **Tier 2 Alert (Error Budget $> 80\%$ consumed)**: Freeze on all non-essential schema changes; root cause triage mandated.
3. **Tier 3 Alert (Error Budget Exhausted)**: Immediate operational review; automated rollback of recent deployments.
