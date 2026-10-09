# MDRAP Phase 5 — Residual Risk Register and Operational Hazards

## 1. Executive Summary & Risk Management Context
In high-stakes financial technology, operational readiness requires an explicit, objective understanding of remaining risks and their mitigation strategies. This register documents all residual risks identified following the completion of Phase 5 pilot validation, their likelihood and impact under **Profile A**, and their associated operational mitigations.

---

## 2. Residual Risk Assessment Matrix

| Risk ID | Hazard / Risk Description | Likelihood | Impact | Severity | Mitigation & Compensating Control | Owner |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **RSK-01** | **Host Hardware Failure under Single-Node Profile A**: Loss of host results in service outage until node reboot or manual failover. | Low | High | **MEDIUM** | Strict Profile A operational boundary declaration; host cold-spare provisioning; target RTO < 3 min; multi-node clustering gate enforced. | SRE Lead |
| **RSK-02** | **Proprietary Exchange Protocol Microburst Quirks**: Live exchange feeds may emit undocumented frame variants not present in replay datasets. | Medium | Medium | **MEDIUM** | Fail-safe normalization parser; unparseable frames routed to quarantine without pipeline crash; daily schema validation probes. | Lead Architect |
| **RSK-03** | **Disk I/O Contention from Heavy Analytical Queries**: Concurrent analytical read queries could stall SQLite WAL checkpoints. | Low | Medium | **LOW** | Enforce `PRAGMA query_only = ON;` on all analytical queries; redirect historical TCA queries to secondary columnar archives (`src/archive.py`). | Database Ops |
| **RSK-04** | **PTP / Host Clock Desynchronization**: Virtualized host clock stepping could cause apparent negative latency measurements. | Low | Low | **LOW** | PTP daemon monitoring; gateway checks $\Delta_{\text{clock}}$ and flags `Reason.CLOCK_DRIFT_NEGATIVE` without corrupting events. | Systems Eng |
| **RSK-05** | **Configuration Drift Across Environments**: Manual tuning of staging configs diverging from production manifests. | Low | Medium | **LOW** | Automated deployment preflight check (`scripts/deploy_pilot.py --check-only`) validates config hash against approved manifest. | DevOps Lead |
| **RSK-06** | **Slow TCP SBE Consumer Buffer Exhaustion**: Lagging downstream consumer causes kernel socket buffer bloat. | Medium | Low | **LOW** | Bounded non-blocking socket dispatch; automatic client disconnect when backlog exceeds 50,000 frames. | Core Eng |

---

## 3. Risk Treatment and Acceptance Summary

1. **Accepted Operational Boundary**: Single-node downtime risk (**RSK-01**) is explicitly accepted by trading stakeholders for the duration of the Controlled Pilot. Production trading algorithms connecting to the pilot must implement client-side failover or local caching.
2. **Technical Boundaries**: No unmitigated high or critical security or data-integrity risks remain.
3. **Escalation Trigger**: If any residual risk manifests as an operational incident during the pilot, the Post-Incident Review (`audit/phase5/post_incident_template.md`) must re-evaluate the risk score.
