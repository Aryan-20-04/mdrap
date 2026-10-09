# Phase 4 Operational Dashboards & Grafana Specifications

**Target Visualization**: Grafana 10+ / Datadog  
**Datasource**: Prometheus (`/metrics`)  
**Timestamp**: 2026-10-09  

---

## 1. Dashboard Layout Structure

The production MDRAP Grafana dashboard is structured across 4 operational panels:

### Panel 1: Ingestion & Pipeline Throughput
- **Query**: `rate(mdrap_events_processed_total[1m])`
- **Visualization**: Time series graph (stacked by venue source).
- **Thresholds**: Green > 5k eps; Yellow < 1k eps; Red = 0 eps.

### Panel 2: Data Quality & Quarantine Rate
- **Query 1**: `sum by (status) (rate(mdrap_events_quality_total[1m]))`
- **Query 2**: `mdrap_quarantine_rate * 100`
- **Visualization**: Gauge + Multi-line chart.
- **Alert**: Quarantine rate > 1.0% triggers P2 Slack/PagerDuty notification.

### Panel 3: Feed Gaps & Backpressure Drops
- **Query**: `increase(mdrap_events_dropped_total[5m])`
- **Visualization**: Stat / Bar gauge.
- **Rule**: Any non-zero value indicates buffer eviction or socket drop.

### Panel 4: Node HA & Cluster State
- **Query**: `mdrap_feed_healthy_count`
- **Visualization**: Single stat status indicator.
- **Alert**: Healthy count < minimum required cluster nodes triggers P1 failover alert.
