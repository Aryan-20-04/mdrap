# MDRAP Phase 5 — Customer Support Model and Operational Escalation

## 1. Executive Summary & Support Philosophy
The Market Data Reliability & Acceleration Platform (MDRAP) operates under a mission-critical support model designed for institutional trading desks, quantitative hedge funds, and risk management divisions. Support operations combine automated telemetry triage, automated sanitized diagnostic bundle generation, and tiered engineering escalation across global trading hours.

---

## 2. Multi-Tier Support Organization

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIER 1: TRADING DESK OPERATIONS / FIRST RESPONSE                            │
│  - Desk-side market data coordinators and trading assistants               │
│  - Responsibilities: Triage consumer connection tickets, reset client tokens│
│  - Response SLA: < 5 Minutes (Market Hours)                                 │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (Escalation: Infrastructure / Latency)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIER 2: SITE RELIABILITY ENGINEERING (SRE) & PLATFORM OPS                   │
│  - Dedicated SRE team with 24/7 on-call rotation                            │
│  - Responsibilities: Host metrics, network routing, failover, disk I/O,     │
│    diagnostic bundle analysis, runbook execution                           │
│  - Response SLA: < 15 Minutes                                               │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (Escalation: Engine Bug / Crash / Fastpath)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIER 3: PRINCIPAL CORE SYSTEMS & DISTRIBUTED SYSTEMS ARCHITECTURE          │
│  - Authors of core C fastpath, SBE framing, and reconciler modules          │
│  - Responsibilities: Hotfix engineering, memory crash forensics, protocol   │
│    discrepancy resolution, kernel bypass tuning                             │
│  - Response SLA: < 1 Hour for SEV-1                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Incident Severity Levels & Service Level Objectives (SLOs)

| Severity | Definition | Target TTA | Target TTR | Communication Cadence |
| :--- | :--- | :--- | :--- | :--- |
| **SEV-1 (Critical)** | Complete market data outage, undetected sequence drop, or corruption affecting active trading. | **< 5 min** | **< 30 min** | Every 15 minutes to executive desk |
| **SEV-2 (Major)** | Partial venue outage (1 feed down, secondary healthy), latency SLO breach (p99 > 1ms). | **< 15 min** | **< 2 hours** | Hourly status update |
| **SEV-3 (Minor)** | Non-blocking telemetry issue, slow historical replay query, single client connection failure. | **< 1 hour** | **< 1 business day** | Daily ticket update |
| **SEV-4 (Inquiry)** | Feature inquiry, new venue entitlement request, configuration consultation. | **< 4 hours** | Next sprint | As requested |

---

## 4. Shift Handoff Protocol (Follow-the-Sun Support)

To provide continuous support across global market trading sessions:
1. **Handoff Times**:
   - Tokyo / APAC to London / EMEA: 07:00 UTC
   - London / EMEA to New York / US: 13:00 UTC
   - New York / US to Tokyo / APAC: 21:00 UTC
2. **Mandatory Handover Checklist**:
   - Review active JIRA tickets and pending pull requests.
   - Verify health status of all pilot hosts via Prometheus/Grafana dashboard.
   - Confirm storage disk utilization across hot partitions (< 75%).
   - Review all quarantine records generated during the preceding session.
