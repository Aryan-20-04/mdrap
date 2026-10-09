# MDRAP Phase 5 — Pilot Feedback Intake, Scoring, and Triage Process

## 1. Executive Summary & Purpose
The controlled production pilot is designed to gather real-world operational feedback from trading desks, quantitative researchers, and SRE teams while strictly isolating risk. This document defines the formal feedback intake channels, severity classification matrix, resolution workflows, and stakeholder sign-off criteria required before advancing beyond the pilot phase.

---

## 2. Feedback Intake Channels

Feedback is ingested through three distinct channels:

1. **Automated Telemetry Feedback**:
   - Automated client disconnect and reconnect counters (`mdrap_consumer_disconnects_total`).
   - Sequence gap query frequency (clients asking for historical replay).
   - Ingress malformed packet logs.
2. **Operational Incident Logs & Tickets**:
   - Tickets filed in JIRA under project `MDRAP` with tag `pilot-feedback`.
   - PagerDuty incident alerts occurring during pilot hours.
3. **Structured Weekly Stakeholder Review**:
   - 30-minute weekly operational meeting with Desk Leads, SRE, and Systems Engineering.

---

## 3. Triage Matrix & Scoring Framework

Every piece of feedback is classified into one of four priority tiers:

| Tier | Category | Description | SLA for Triage / Fix |
| :--- | :--- | :--- | :--- |
| **Tier 1 (Blocker)** | Correctness or Invariant Violation | Any report of sequence gap, undetected duplicate, data corruption, or process crash. | **Immediate freeze / < 24h fix** |
| **Tier 2 (Operational)**| Latency or Stability Defect | Tail latency spike (p99 > 500 µs), socket timeout, slow replay response (> 2 sec). | **< 48 hours** |
| **Tier 3 (Ergonomic)** | SDK or Protocol Usability | Unclear SBE schema documentation, inconvenient timestamp formats, confusing CLI output. | **Next release cycle** |
| **Tier 4 (Enhancement)**| New Capability Request | Request for new feed venue adapter, additional options Greeks, multi-node clustering. | **Roadmap backlog (Post-Phase 5)**|

---

## 4. Consumer Pilot Scorecard & Exit Survey

Prior to approving graduation from the pilot phase, each participating trading desk completes the following survey:

- [ ] **Data Correctness**: Did all canonical prices and quotes match the expected exchange book? (Yes / No)
- [ ] **Latency Predictability**: Did market data delivery satisfy your algorithmic execution latency requirements? (Yes / No)
- [ ] **Reconnection & Resilience**: Did your consumers recover automatically from scheduled restarts? (Yes / No)
- [ ] **Tooling & Operability**: Were diagnostic tools and support channels responsive? (Yes / No)
- [ ] **Formal Sign-Off**: Trading Desk Head Signature.
