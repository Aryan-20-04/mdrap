# Phase 5 Production Dashboard & Operator View

**Exposition Endpoint**: `GET /metrics` (Prometheus 0.0.4 text format)  
**Operator HTTP Endpoint**: `GET /health`  
**Date**: 2026-10-09  

---

## 1. Operator Triage Dashboard Panels

The operations console provides immediate answers to the 12 critical operational questions:

1. **Is the service running?**: `process_uptime_seconds > 0`
2. **Is it ready to accept work?**: `/health` status == `"healthy"`
3. **Is feed progressing?**: `rate(mdrap_events_processed_total[1m]) > 0`
4. **Is processing progressing?**: Egress events updating monotonically.
5. **Are events being persisted?**: IngestLog segment file sizes advancing.
6. **Are consumers keeping up?**: `consumer_lag_events == 0`
7. **Are sequence gaps present?**: `mdrap_sequence_gaps_total`
8. **Is the service degraded?**: `mdrap_feed_healthy_count >= 1`
9. **Is recovery in progress?**: `node_state == "SYNCING"`
10. **Are entitlements/accounting failing?**: `/metrics` accounting failure counters.
11. **Is resource exhaustion approaching?**: Host memory RSS < 80%.
12. **Is the deployed version approved?**: Commit hash matches `release_manifest.json`.
