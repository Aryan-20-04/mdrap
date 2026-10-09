# MDRAP Phase 4 Production Operations Runbooks

**Audience**: Site Reliability Engineers, Market Data Operations, System Administrators  
**Platform Version**: MDRAP v3.0  
**Timestamp**: 2026-10-09  

---

## 1. Runbook A: Cold Start & Normal Initialization

1. Verify environment prerequisites:
   - Python 3.11+ runtime.
   - Storage volume mounted with write permissions on `/var/lib/mdrap`.
2. Inspect configuration:
   - Check `mdrap.toml` for correct venue endpoints, credentials, and buffer sizes.
3. Launch primary daemon:
   ```bash
   python -m mdrap.service --config /etc/mdrap/mdrap.toml --role primary
   ```
4. Verify health endpoint:
   ```bash
   curl -s http://localhost:8080/health | jq .
   ```
   Ensure `state == "STREAMING"` and `gaps_detected == 0`.

---

## 2. Runbook B: Handling Upstream Feed Gaps

1. Check Prometheus metrics or logs for `[ingress] Sequence gap of N detected`.
2. Determine if gap is transient or persistent:
   - If transient: Ingress adapter will log missing count and continue processing live stream.
   - If persistent or > 10,000 packets: Feed may require TCP replay snapshot.
3. Trigger upstream replay request if supported by venue protocol.

---

## 3. Runbook C: Emergency Failover & Node Isolation

If the primary node exhibits hardware faults or network degradation:
1. Promote standby node immediately:
   ```bash
   # CLI / API manual promotion trigger
   python -m mdrap.cli failover promote --node-id standby-1 --reason "manual_evacuation"
   ```
2. Verify standby assumes PRIMARY role with incremented epoch:
   ```bash
   curl -s http://standby-1:8080/ha/status | jq .
   ```
3. Fence out demoted primary:
   - Stop service or isolate network interface to prevent stale writes.
   ```bash
   systemctl stop mdrap-primary
   ```

---

## 4. Runbook D: Post-Crash WAL Recovery

1. If the host crashed abruptly, inspect `/var/lib/mdrap/wal/`:
   ```bash
   ls -lh /var/lib/mdrap/wal/
   ```
2. Start MDRAP in recovery validation mode:
   ```bash
   python -m mdrap.cli wal verify --log-dir /var/lib/mdrap/wal/
   ```
3. The engine will inspect all `.log` segments, repair or truncate any incomplete tail frame, report recovered record count, and exit with code 0 upon integrity confirmation.
