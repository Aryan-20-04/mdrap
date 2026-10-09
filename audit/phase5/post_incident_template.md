# MDRAP Phase 5 — Post-Incident Review Template (PIR)

## 1. Document Overview & Purpose
This template provides the mandatory post-incident review (PIR) framework for the Market Data Reliability & Acceleration Platform (MDRAP). Any SEV-1 or SEV-2 incident must produce a completed PIR within 48 hours of incident mitigation. The PIR focus is strictly blameless, mechanistic, and oriented toward structural remediation and automated regression prevention.

---

## 2. Incident Summary Metadata

| Field | Detail |
| :--- | :--- |
| **Incident Reference** | `INC-YYYYMMDD-XX` |
| **Severity Level** | `SEV-1 (Critical Outage)` / `SEV-2 (Degraded Production)` |
| **Incident Title** | Short summary of incident impact (e.g., *IngestLog Inodes Exhaustion on Feed A*) |
| **Incident Commander (IC)** | Name / On-call rotation identifier |
| **Leading Investigator** | Name / SRE or Systems Engineer |
| **Date & Time of Outage** | YYYY-MM-DD HH:MM:SS UTC |
| **Date & Time of Mitigation** | YYYY-MM-DD HH:MM:SS UTC |
| **Total TTD (Time to Detect)**| MM minutes (Target: < 2 min for SEV-1) |
| **Total TTA (Time to Acknowledge)** | MM minutes (Target: < 5 min) |
| **Total TTR (Time to Remediate)**| MM minutes (Target: < 15 min for SEV-1) |
| **Customer / Consumer Impact** | E.g., Consumer dropped 42 frames; sequence gap detected and backfilled |

---

## 3. Executive Impact Summary
Provide a high-level narrative describing:
1. What was the observed symptom by external consumers or feeds?
2. Did any data corruption, silent loss, or sequence reordering occur?
3. What was the customer/business operational exposure?

*Example Statement*:
> Between 14:10 UTC and 14:18 UTC, MDRAP Node 01 experienced persistent WAL lock contention following a sudden disk I/O burst. Ingest throughput dropped below SLO threshold (3,000 eps -> 412 eps). No data was corrupted or silently dropped. Downstream SBE consumers experienced an 8-minute delivery delay. All 4,120 buffered events were verified through SHA-256 Merkle audit and delivered sequentially.

---

## 4. Timeline of Events (UTC)
Chronological record of system events, alerts, operator interventions, and recovery steps.

| Timestamp (UTC) | Source / Component | Event Description & Operator Actions |
| :--- | :--- | :--- |
| `14:10:02` | Alertmanager | `MDRAPThroughputCollapse` triggered (Ingest rate < 1,000 eps for 60s). |
| `14:11:15` | PagerDuty | On-call engineer alerted; incident channel `#mdrap-incident-live` opened. |
| `14:12:30` | Engineer / CLI | Diagnostic bundle gathered via `python scripts/diagnostic_bundle.py --out /var/log/bundles/`. |
| `14:14:00` | SRE On-call | Identified high disk write queue depth on WAL partition. Fsync grouping switched. |
| `14:16:45` | System / Storage | IngestLog drained backlogged buffer; queue size returned to 0. |
| `14:18:00` | Prometheus | Rate returned to baseline (3,250 eps); alert cleared. Incident mitigated. |

---

## 5. Root Cause Analysis (5 Whys)

1. **Why did consumers experience latency?**
   - Ingest pipeline backpressure accumulated due to slow IngestLog disk writes.
2. **Why were IngestLog disk writes slow?**
   - The storage layer was configured with synchronous single-event `os.fsync()` under an unexpected 5x surge in tick volume.
3. **Why was single-event fsync active under burst conditions?**
   - Default pilot configuration utilized `fsync_policy="always"` rather than `fsync_policy="grouped_by_size"`.
4. **Why was the group sync policy not dynamically engaged?**
   - The queue-depth adaptive flush controller was disabled in the staging deployment configuration file.
5. **Why was the adaptive flush setting disabled in staging?**
   - Staging configuration drift: an experimental test override was committed to the staging profile without release gate review.

---

## 6. Correctness & Data Integrity Audit
Verify that the core MDRAP invariants were maintained during the incident:

- [ ] **Zero Silent Loss**: Verified against IngestLog monotonic sequence counter (`audit_seq_gaps == 0`).
- [ ] **Zero Reordering**: Monotonic sequence progression confirmed by independent consumer log.
- [ ] **Zero Silent Corruption**: IngestLog CRC32 / SBE frame validation passed with zero invalid checksums.
- [ ] **Lineage Preserved**: Every event processed during degraded state maps to a verifiable raw source hash.
- [ ] **Quarantine Accounting**: All rejected frames properly accounted for in the quarantine store.

---

## 7. What Went Well vs. What Went Wrong

### What Went Well
- Monitoring alerts fired within 60 seconds of backpressure onset.
- Zero-loss architecture prevented any frame drop or silent discard.
- Automated diagnostic script captured full anonymized telemetry bundle without manual triage delays.

### What Went Wrong
- Runbook did not explicitly mandate checking adaptive fsync flags during pre-flight.
- Dashboard did not highlight filesystem write queue depth on the primary view.

---

## 8. Preventive Action Items & JIRA Tracking

| Action Item | Owner | Target Date | Tracking ID | Priority |
| :--- | :--- | :--- | :--- | :--- |
| Enforce `fsync_policy="grouped_by_size"` in deployment linter | SRE Lead | YYYY-MM-DD | `MDRAP-1042` | P1 |
| Add disk queue depth gauge to primary Prometheus dashboard | Observability | YYYY-MM-DD | `MDRAP-1043` | P2 |
| Add automated drift check against deployment YAML in CI | DevOps | YYYY-MM-DD | `MDRAP-1044` | P1 |
