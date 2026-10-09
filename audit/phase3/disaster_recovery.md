# MDRAP Phase 3 — Disaster Recovery & Failover Runbook

**Document Identifier**: `MDRAP-DR-P3-001`  
**Date**: October 9, 2026  

---

## 1. Failover Lifecycle & Targets

| Metric | Target | Measured / Tested | Notes |
|---|---|---|---|
| **RTO (Recovery Time Objective)** | < 1.0 s | 0.05–0.10 s | Heartbeat detection + state machine promotion |
| **RPO (Recovery Point Objective)** | 0 lost committed events | 0 under semi-sync replication | Standby enters `SYNCING` if sequence gap exists |

---

## 2. Emergency Operational Procedures

1. **Primary Node Failure**:
   - Standby detects primary silence after `heartbeat_timeout_s`.
   - Standby checks sequence parity. If caught up, standby promotes to `PRIMARY` (epoch incremented).
   - Downstream consumers detect `EPOCH_TRANSITION` and reconnect to new primary.
2. **Old Primary Rejoin**:
   - Recovered former primary reconnects as `STANDBY`.
   - Former primary receives heartbeat with higher epoch and demotes itself cleanly.
   - Any attempt by the former primary to write with its old fencing token is rejected with `StaleEpochError`.
