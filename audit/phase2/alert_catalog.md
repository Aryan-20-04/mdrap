# MDRAP Phase 2 — Production Alerting Rules & Catalog

**Document Identifier**: `MDRAP-ALERT-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: Alertmanager, SRE Runbooks  

---

## 1. Alert Hierarchy & Severity

- **SEV-1 (CRITICAL - Page On-Call)**: Imminent risk to data integrity, poisoned WAL, engine crash, or prolonged zero-throughput stall during market hours.
- **SEV-2 (HIGH - Urgent Ticket)**: Significant backpressure drops, multiple feed disconnections, elevated tail latency.
- **SEV-3 (MEDIUM - Next Business Day)**: Single client eviction, transient reconnect bursts, disk space warning (>80%).

---

## 2. Alert Definitions

| Alert Name | Severity | Condition / Threshold | Evaluation Window | Mitigation Runbook |
|---|---|---|---|---|
| `WalPoisonedCritical` | **SEV-1** | `mdrap_wal_poisoned == 1` | Immediate (0s) | Ingest halted. Inspect disk I/O, run `mdrap salvage`, restart service. |
| `ReadinessProbeFailing` | **SEV-1** | `probe_success{endpoint="/readiness"} == 0` | 30s | Node marked unready. Verify database lock, disk space, and worker crashes. |
| `FeedSilenceAll` | **SEV-1** | `sum(mdrap_watchdog_sources{status="HEALTHY"}) == 0` | 60s | Upstream market connectivity lost. Check internet gateway, venue API status. |
| `HighSubscriberDrops` | **SEV-2** | `rate(mdrap_ws_subscriber_drops_total[1m]) > 50` | 2m | Downstream WebSocket consumers lagging. Identify and isolate slow subscribers. |
| `HighClientDrops` | **SEV-2** | `rate(mdrap_client_queue_drops_total[1m]) > 100` | 2m | TCP clients stalled. Check client CPU / network throughput. |
| `ProcessingLatencyHigh` | **SEV-2** | `histogram_quantile(0.99, rate(mdrap_engine_step_latency_us[5m])) > 5000` | 5m | Tail latency > 5ms. Inspect CPU contention, lock starvation, or Python GC pause. |
| `WorkerRestartHigh` | **SEV-2** | `increase(mdrap_supervisor_worker_restarts_total[5m]) > 3` | 5m | Supervised worker crashing repeatedly. Check logs for recurring unhandled exceptions. |
| `DiskUsageHigh` | **SEV-3** | `node_filesystem_free_bytes / node_filesystem_size_bytes < 0.15` | 10m | Disk under 15% free. Rotate old IngestLog segments or archive to cold storage. |
