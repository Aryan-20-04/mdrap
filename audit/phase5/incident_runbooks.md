# Phase 5 Production Incident Runbooks

**Date**: 2026-10-09  
**Platform**: MDRAP v3.0.0  

---

## 1. Runbook INC-01: Ingestion Feed Stalled / Silent

1. Check feed health:
   ```bash
   curl -s http://localhost:8080/health | jq .active_feeds_count
   ```
2. Verify network interface packets:
   - Check if exchange connection timed out or socket dropped.
3. Check watchdog alerts:
   - Inspect `/metrics` for `mdrap_source_silence_alerts_total`.
4. If upstream issue: Notify venue operations desk.

---

## 2. Runbook INC-02: Storage Write Outage / Disk Full

1. Check disk utilization:
   ```bash
   df -h /var/lib/mdrap/
   ```
2. If disk is >95% full:
   - Archive sealed `.log` segments to backup volume.
   - Run WAL segment cleaner.
3. If filesystem error:
   - Verify advisory lock file `.lock`.
   - Engine will reject uncommitted ACKs rather than claiming false durability.

---

## 3. Runbook INC-03: Split-Brain Failover Collision

1. Check active epoch across nodes:
   ```bash
   curl -s http://node-1:8080/ha/status | jq .epoch
   curl -s http://node-2:8080/ha/status | jq .epoch
   ```
2. The node with the higher epoch and valid fencing token remains primary.
3. Demote stale primary immediately via CLI:
   ```bash
   python -m mdrap.cli failover demote --node-id node-1
   ```
