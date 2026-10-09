# MDRAP Phase 2 — Production Metrics Catalog

**Document Identifier**: `MDRAP-METRICS-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: Prometheus Exporter, Health Endpoints, Runtime Engine  

---

## 1. System & Ingest Metrics

| Metric Name | Type | Description | Labels / Dimensions | Target Alert |
|---|---|---|---|---|
| `mdrap_uptime_seconds` | Gauge | Uptime of the running MDRAP instance | none | ServiceRestart |
| `mdrap_ingest_events_total` | Counter | Total raw market events ingested | `source`, `event_type` | IngestStall |
| `mdrap_ingest_bytes_total` | Counter | Total raw wire bytes received | `source` | FeedNetworkDrop |
| `mdrap_canonical_events_total` | Counter | Total validated canonical events published | `instrument_id`, `status` | AnomalyRateHigh |
| `mdrap_engine_step_latency_us` | Histogram | Per-event engine processing latency (p50, p95, p99) | `instrument_id` | ProcessingLatencySpike |

---

## 2. Queue & Backpressure Metrics

| Metric Name | Type | Description | Labels / Dimensions | Target Alert |
|---|---|---|---|---|
| `mdrap_client_queue_drops_total` | Counter | Total frames dropped due to TCP client queue overflow | `client_id` | ClientQueueFull |
| `mdrap_ws_subscriber_drops_total` | Counter | Total messages dropped due to WS subscriber queue overflow | none | SubscriberQueueOverflow |
| `mdrap_feed_queue_drops_total` | Counter | Total raw frames evicted from feed manager queue | `venue` | FeedIngestBackpressure |
| `mdrap_active_tcp_clients` | Gauge | Count of actively connected TCP streaming clients | none | ClientExhaustion |
| `mdrap_active_ws_subscribers` | Gauge | Count of active WebSocket streaming connections | none | SubscriberExhaustion |

---

## 3. Storage & Durability Metrics

| Metric Name | Type | Description | Labels / Dimensions | Target Alert |
|---|---|---|---|---|
| `mdrap_wal_segments_count` | Gauge | Total active IngestLog segment files on disk | none | SegmentRetentionHigh |
| `mdrap_wal_bytes_total` | Gauge | Total bytes committed to IngestLog WAL | none | DiskSpaceLow |
| `mdrap_wal_poisoned` | Gauge | Binary flag (1 if WAL is poisoned due to I/O error) | none | WalPoisonedCritical |
| `mdrap_sqlite_commits_total` | Counter | Total batch commits executed to SQLite projection | none | StorageCommitStall |
| `mdrap_sqlite_conflicts_total` | Counter | Total conflict rollbacks during concurrent writes | none | DatabaseContention |
