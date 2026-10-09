# Phase 4 Production Operator Handoff Guide

**Target Audience**: Level 2 / Level 3 Operations Engineers, SREs  
**System**: Market Data Reliability & Acceleration Platform (MDRAP v3.0)  
**Date**: 2026-10-09  

---

## 1. System Overview & Component Topography

MDRAP sits directly between raw upstream exchange feeds and internal trading desks / databases:
```text
[External Feeds: ITCH / Polygon / Binance / Direct UDP]
                          │
                          ▼
            [Feed Ingress Adapters]
                          │ (RawEvent)
                          ▼
            [Schema Normalization]
                          │ (CanonicalEvent)
                          ▼
             [Quality Rule Engine] ───(INVALID)──► [Quarantine Store]
                          │ (VALID / SUSPICIOUS)
                          ▼
                 [Reconciler]
                          │ (CanonicalDecision)
              ┌───────────┴───────────┐
              ▼                       ▼
     [IngestLog WAL]         [SBE Ring / SHM]
              │                       │
              ▼                       ▼
   [Cold Replay / Recovery]  [Quant Consumers (C++/Java/Py)]
```

---

## 2. Daily Operational Health Checklist

1. **Morning Pre-Market Check (07:00 EST)**:
   - Check process state: `systemctl status mdrap`
   - Check HTTP health: `curl http://localhost:8080/health` (ensure `status == "healthy"`).
   - Check sequence watermarks: Verify `gaps_detected == 0` on all primary feeds.
   - Verify WAL disk availability: Ensure `/var/lib/mdrap` has > 50 GB free disk space.
2. **Intraday Monitoring**:
   - Prometheus alerts configured for:
     - `mdrap_events_dropped_total > 0`
     - `mdrap_quarantine_rate > 0.01`
     - `mdrap_feed_healthy_count < 2`
3. **Evening Post-Market Maintenance (17:30 EST)**:
   - Verify all segments are synced and closed.
   - Rotate or archive sealed `.log` segments older than 7 days.
   - Run audit trail verification: `python -m mdrap.cli audit verify`.

---

## 3. Incident Triage Escalation Matrix

| Symptom | Severity | Immediate Action | Secondary Escalation |
| :--- | :--- | :--- | :--- |
| **Ingress Sequence Gap Spike** | P2 | Inspect exchange feed status; check physical link bandwidth. | Contact exchange connectivity desk for replay snapshot. |
| **Primary Node Heartbeat Timeout** | P1 | Verify standby auto-promoted; ensure downstream consumers repointed. | Check primary host hardware / network interface. |
| **High Quarantine Rate (>1%)** | P2 | Inspect `SELECT * FROM quarantine ORDER BY timestamp DESC LIMIT 10;`. | Check if exchange issued emergency price band / circuit breaker. |
| **IngestLog Disk Space Warning (>80%)** | P2 | Archive sealed `.log` segments to secondary tier storage. | Increase volume capacity. |
