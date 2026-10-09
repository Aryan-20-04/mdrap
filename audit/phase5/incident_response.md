# Phase 5 Incident Response Framework & Severity Classification

**Date**: 2026-10-09  
**Platform**: MDRAP v3.0.0  

---

## 1. Incident Severity Definitions

| Severity | Definition | Target Triage Time | Target Mitigation Time |
| :--- | :--- | :--- | :--- |
| **SEV-1 (Critical)** | Core engine crash; sequence gap emitted; confirmed corruption; split-brain writer collision. | < 5 minutes | < 15 minutes |
| **SEV-2 (High)** | Upstream feed disconnect (>2s); high quarantine rate (>1%); standby node offline; consumer lag spike. | < 15 minutes | < 1 hour |
| **SEV-3 (Medium)** | Non-blocking metric scrape error; slow consumer drop on single client session; non-fatal warning. | < 1 hour | < 4 hours |
| **SEV-4 (Low)** | Minor documentation discrepancy; non-urgent configuration drift; test environment anomaly. | Next business day | Next sprint |

---

## 2. Escalation & Communication Protocol

1. **Detection**: Automated Prometheus alert fires or operator observes metric anomaly.
2. **On-Call Pager**: PagerDuty alerts Market Data SRE on-call engineer.
3. **War Room**: Slack `#mdrap-incidents` established; incident commander assigned.
4. **Safety Rule**: Never restart blindly if data corruption or WAL lock contention is suspected. Run `python scripts/diagnostic_bundle.py` first.
