# MDRAP Service Level Objectives (SLOs) & Production Observability Catalog

**Version**: 2.6.0  
**Status**: Institutional Production Standard  
**Last Updated**: October 2026  

---

## 1. Overview & Architecture

The Market Data Reliability & Acceleration Platform (MDRAP) operates as an ultra-low-latency sidecar preceding institutional algorithmic execution engines, quantitative analytics, and audit databases. Because corrupted or delayed ticks can directly cause erroneous execution, false arbitrage, or financial loss, MDRAP enforces formal **Service Level Objectives (SLOs)**, quantified **Error Budgets**, and zero-syscall **Observability Standards**.

### Observability Design Principles
1. **Zero Hot-Path Syscalls**: Metrics collection uses atomic in-memory accumulators and bitmasks; no disk I/O, network calls, or kernel locks occur during hot-path normalization and validation.
2. **Decoupled Pull Model**: Formatting to Prometheus exposition format (v0.0.4) occurs strictly upon HTTP `/metrics` scrape requests.
3. **Asynchronous Heavy Computations**: Cryptographic Merkle tree integrity verification runs in a background worker thread (`prometheus-audit-verifier`), guaranteeing scrape latencies $<5\,\text{ms}$ without CPU starvation (`OPS-02`).
4. **Deterministic Lineage**: Every emitted metric traces directly to venue sequence counters and nanosecond-resolution UTC hardware timestamps.

---

## 2. Institutional Service Level Objectives (SLOs)

| Objective | Target | Measurement Window | Alert Severity | Action on Breach |
|---|---|---|---|---|
| **Data Durability & Non-Drop Rate** | $\ge 99.999\%$ of valid ticks stored & broadcast | 30-day rolling | **CRITICAL (P1)** | Freeze order submission; drain SHM buffer; trigger automated replay. |
| **Silent Drop Prevention** | $0$ dropped events without quarantine / dead-letter | Real-time | **CRITICAL (P1)** | Immediate halt of primary pipeline; activate failover feed. |
| **Zero-Copy Processing Latency (p50)** | $\le 15.0\,\mu\text{s}$ (C fastpath) / $\le 85.0\,\mu\text{s}$ (Python) | 1-hour rolling | **WARNING (P3)** | Log thread scheduling anomalies; inspect CPU core affinity. |
| **Tail Latency (p99)** | $\le 250.0\,\mu\text{s}$ (C fastpath) / $\le 850.0\,\mu\text{s}$ (Python) | 1-hour rolling | **HIGH (P2)** | Inspect SHM watermark occupancy and memory bus saturation. |
| **Max Tail Latency (p99.9)** | $\le 1.0\,\text{ms}$ | 1-hour rolling | **HIGH (P2)** | Inspect GC pauses, thread preemption, and OS page faults. |
| **Feed Silence Detection** | $\le 1.0\,\text{s}$ of absolute venue silence | Real-time | **HIGH (P2)** | Watchdog declares `SILENT`; switches consensus to secondary. |
| **Automated Failover Transition** | $\le 5.0\,\text{ms}$ post silence detection | Real-time | **HIGH (P2)** | Fallback to secondary venue BBO without dropping book state. |
| **Monotonic Gap Detection & Stitching** | $\le 1.0\,\text{ms}$ detection; $\le 50.0\,\text{ms}$ TCP replay | Real-time | **HIGH (P2)** | Transition state machine `GAP_DETECTED -> RECOVERING -> LIVE`. |
| **Cryptographic Merkle Audit Integrity** | $100\%$ valid (`mdrap_audit_verified_status == 1`) | Continuous (300s cache) | **CRITICAL (P1)** | Alert compliance officer; isolate database partition. |
| **Prometheus Scrape Response Time** | $\le 5.0\,\text{ms}$ (p99) | 5-minute scrape interval | **WARNING (P3)** | Verify background audit worker is not blocking HTTP server thread. |

---

## 3. Production Metrics Catalog

### 3.1 Pipeline Throughput & Quality Metrics

| Metric Name | Type | Unit | Description | SLO / Alert Threshold |
|---|---|---|---|---|
| `mdrap_events_processed_total` | Counter | Events | Monotonic count of all raw market events normalized by gateway. | Rate $> 0$ during trading hours. |
| `mdrap_events_dropped_total` | Counter | Events | Total unrecoverable dropped events. | **Must be 0**. Alert if $> 0$. |
| `mdrap_events_quality_total{status="VALID"}` | Counter | Events | Events passing 100% of validation rules. | Baseline: $> 95\%$ of stream. |
| `mdrap_events_quality_total{status="SUSPICIOUS"}` | Counter | Events | Anomalous events (spread blowout, excessive size). | Alert if $> 5\%$ of total stream. |
| `mdrap_events_quality_total{status="INVALID"}` | Counter | Events | Corrupted, crossed, or schema-violating events quarantined. | Alert if $> 2\%$ of total stream. |
| `mdrap_quarantine_rate` | Gauge | Ratio (0.0–1.0) | Ratio of quarantined (`SUSPICIOUS + INVALID`) to total events. | Alert if $> 0.05$ sustained $> 30\,\text{s}$. |

### 3.2 Feed Health & Multi-Venue Consensus Metrics

| Metric Name | Type | Unit | Description | SLO / Alert Threshold |
|---|---|---|---|---|
| `mdrap_feed_uptime_seconds` | Gauge | Seconds | MDRAP engine uptime in seconds. | Alert on unexpected restart. |
| `mdrap_feed_healthy_count` | Gauge | Feeds | Number of currently active, non-silent market feeds. | Alert if $< \text{total feeds} - 1$. |
| `mdrap_feed_monitored_total` | Gauge | Feeds | Total configured feeds monitored by the watchdog daemon. | Static configuration check. |
| `mdrap_feed_status{source="<NAME>"}` | Gauge | Enum (0/1) | Source status: `1 = HEALTHY`, `0 = DEGRADED/SILENT/DEAD`. | Alert immediately if primary feed `= 0`. |
| `mdrap_cross_feed_disagreements_total` | Counter | Events | Total multi-venue consensus divergences (prices differing $> \text{threshold}$). | Alert on divergence spike $> 100/\text{min}$. |

### 3.3 Zero-Copy Shared Memory (SHM) Telemetry

| Metric / Field | Type | Unit | Description | SLO / Alert Threshold |
|---|---|---|---|---|
| `heartbeat_ts` (Cache Line 2, offset 64) | Epoch Float | Seconds | Publisher nanosecond-precision heartbeat updated every 512 ticks or 100ms. | Stale if `now - heartbeat_ts > 4.0s`. |
| `dropped_ticks` (Cache Line 2, offset 72) | Counter | Events | Total ticks dropped due to ring buffer full conditions. | **Must be 0**. Alert if $> 0$. |
| `watermark_flag` (Cache Line 2, offset 80) | Bitmask (0x01) | Flag | Watermark warning bitflag set when slowest reader lags $> 80\%$ buffer capacity. | Alert if set $> 5.0\,\text{s}$. |
| `overrun_stats.total_laps` | Counter | Laps | Number of times a slow consumer was lapped by the ring publisher. | Alert if $> 0$ for high-priority consumers. |
| `overrun_stats.skipped_ticks` | Counter | Ticks | Total ticks skipped forward by slow consumers catching up. | Telemetry for algorithmic consumer tuning. |

### 3.4 Feed Recovery & State Machine Telemetry (`src/mdrap/recovery.py`)

| Metric / Field | Type | Unit | Description | SLO / Alert Threshold |
|---|---|---|---|---|
| `state` | Enum | State | Current connection lifecycle state (`LIVE`, `RECOVERING`, `FAILED_RECOVERY`). | Alert if `FAILED_RECOVERY`. |
| `state_transitions` | Counter | Count | Total connection and recovery state transitions. | Rate should stabilize once live. |
| `gaps_detected` | Counter | Gaps | Total sequence jumps detected (`seq > expected_seq`). | Alert if $> 10/\text{min}$ (indicates network drop). |
| `replays_requested` | Counter | Requests | Total TCP/archive replay requests dispatched to feed. | Must match gaps detected. |
| `replays_completed` | Counter | Completions | Total successful gap healings and backfill drains. | Must equal `replays_requested`. |
| `recovery_failures` | Counter | Failures | Unrecoverable gap failures (e.g. buffer saturation). | **Must be 0**. Alert if $> 0$. |
| `stale_snapshots_rejected` | Counter | Rejections | Snapshots with timestamps prior to already processed stream ticks. | Alert if $> 0$. |
| `dedup_dropped` | Counter | Packets | Out-of-order or duplicate recovery packets discarded in $O(1)$. | Normal during overlapping replay windows. |
| `in_order_dispatched` | Counter | Packets | Packets delivered downstream in strictly monotonic sequence order. | Monotonically strictly increasing. |

### 3.5 Cryptographic Durability & Output Sink Metrics

| Metric Name | Type | Unit | Description | SLO / Alert Threshold |
|---|---|---|---|---|
| `mdrap_audit_verified_status` | Gauge | Enum (0/1) | Merkle audit log verification status (`1 = VALID`, `0 = CORRUPT`). | **Must be 1**. Immediate P1 alert if `0`. |
| `mdrap_kafka_sink_lag_events` | Gauge | Events | Durable output sink checkpoint lag behind SQLite commit head. | Alert if lag $> 10,000$ events. |
| `mdrap_kafka_sink_produced_total` | Counter | Events | Total events acknowledged by downstream Kafka/Redpanda cluster. | Must track `mdrap_events_processed_total`. |
| `mdrap_kafka_sink_dropped_total` | Counter | Events | Total events dropped or routed to dead-letter storage by sink. | Alert if $> 0$. |

### 3.6 Alert Delivery & API Operational Metrics

| Metric Name | Type | Unit | Description | SLO / Alert Threshold |
|---|---|---|---|---|
| `mdrap_alert_delivery_total{status="delivered"}` | Counter | Alerts | Successfully dispatched webhooks/Slack/PagerDuty notifications. | Tracks generated alerts. |
| `mdrap_alert_delivery_total{status="failed"}` | Counter | Alerts | Failed external alert deliveries (SSRF blocked, network timeout). | Alert if failure rate $> 1\%$. |
| `mdrap_alert_delivery_backlog` | Gauge | Alerts | Pending alert notifications queued for delivery. | Alert if backlog $> 100$. |
| `mdrap_api_requests_total` | Counter | Requests | Total HTTP/WebSocket requests by HTTP method and route endpoint. | Baseline traffic tracking. |
| `mdrap_api_errors_total` | Counter | Requests | HTTP 4xx/5xx responses partitioned by method and route endpoint. | Alert if 5xx rate $> 0.1\%$. |
| `mdrap_api_latency_seconds_avg` | Gauge | Seconds | Rolling average API request latency. | Alert if $> 0.05\,\text{s}$ ($50\,\text{ms}$). |

---

## 4. Runbook Procedures for SLO Breaches

### 4.1 Breach: `mdrap_events_dropped_total > 0` (P1 CRITICAL)
1. **Immediate Action**: Algorithmic execution engines must pause order entry (`HALT_ALL_DESKS`).
2. **Investigation**:
   - Inspect `data/logs/mdrap.log` for unhandled storage exceptions or SHM ring buffer overruns.
   - Run `python cli.py audit verify --db <path>` to confirm state consistency.
   - Review quarantined items in SQLite table `quarantine_events`.
3. **Recovery**:
   - Restart daemon; state machine will automatically initiate sequence replay from journal.

### 4.2 Breach: `mdrap_feed_status{source="PRIMARY"} == 0` (P2 HIGH)
1. **Immediate Action**: Watchdog automatically transitions consensus to secondary venue (`FEED_SECONDARY`).
2. **Investigation**:
   - Check raw PCAP/UDP multicast packet reception on network interface: `tcpdump -i eth0 udp port <PORT>`.
   - Verify upstream venue multicast heartbeat packets.
3. **Resolution**:
   - Upon feed silence restoration, watchdog automatically clears `SILENT` flag and resumes cross-venue reconciliation.

### 4.3 Breach: `mdrap_audit_verified_status == 0` (P1 CRITICAL)
1. **Immediate Action**: Notify risk and compliance officers. Market data log has been tampered with or experienced unrecoverable disk corruption.
2. **Investigation**:
   - Identify corrupted Merkle leaf: `python cli.py audit verify --verbose`.
   - Inspect underlying filesystem integrity (SMART errors, SQLite page corruption).
3. **Recovery**:
   - Restore SQLite database and raw archive from immutable daily backup snapshot.
