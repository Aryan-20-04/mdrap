# MDRAP Phase 5 — Resource Saturation and Alerting Rules Specification

## 1. Executive Summary & Alerting Philosophy
This document specifies the institutional Prometheus and Alertmanager rule set designed to detect infrastructure, memory, disk, and buffer saturation before data processing guarantees or latency SLOs degrade. Alerts follow strict tiered escalation: **WARNING (P3/P2)** triggers automated runbooks or Slack notification, while **CRITICAL (P1)** immediately pages the on-call SRE and engages backpressure safeguards.

---

## 2. Saturation Alert Definitions

### 2.1 CPU Utilization & Thread Contention
- **Rule Name**: `MDRAPHostCPUSaturation`
- **Severity**: `WARNING`
- **Condition**: Total CPU utilization exceeds 85% across engine cores for > 60 seconds.
- **PromQL**:
  ```promql
  avg(rate(node_cpu_seconds_total{mode!="idle"}[1m])) * 100 > 85
  ```
- **Operational Impact**: Context switching delays, potential queue build-up in ingress socket buffers.
- **Remediation**: Check for competing background processes, verify CPU core pinning.

---

### 2.2 Memory Saturation and Leak Detection
- **Rule Name**: `MDRAPMemoryLeakDetected`
- **Severity**: `CRITICAL`
- **Condition**: Process RSS exceeds 750 MB OR exhibits continuous upward slope (> 20 MB/hr) over 3 consecutive hours.
- **PromQL**:
  ```promql
  process_resident_memory_bytes{job="mdrap"} > 786432000
    or
  deriv(process_resident_memory_bytes{job="mdrap"}[1h]) > 5555
  ```
- **Operational Impact**: Risk of kernel OOM-killer terminating MDRAP engine.
- **Remediation**: Capture heap profile via `diagnostic_bundle.py`, inspect cache size of reconciler and bloom filter.

---

### 2.3 Disk Storage Partition Exhaustion
- **Rule Name**: `MDRAPDiskPartitionLowSpace`
- **Severity**: `WARNING` at 80% full, `CRITICAL` at 90% full.
- **PromQL (Critical)**:
  ```promql
  (node_filesystem_free_bytes{mountpoint="/var/data/mdrap"} /
   node_filesystem_size_bytes{mountpoint="/var/data/mdrap"}) * 100 < 10
  ```
- **Operational Impact**: Storage write failure, IngestLog halting, pipeline emergency pause.
- **Remediation**: Execute retention archive script (`src/archive.py`) to compress older `.seg` files to secondary cold storage.

---

### 2.4 Disk Write I/O Contention & Stalling
- **Rule Name**: `MDRAPDiskWriteLatencySpike`
- **Severity**: `WARNING`
- **Condition**: Disk average write latency exceeds 15 ms for > 30 seconds.
- **PromQL**:
  ```promql
  rate(node_disk_write_time_seconds_total[1m]) /
  rate(node_disk_writes_completed_total[1m]) > 0.015
  ```
- **Operational Impact**: IngestLog fsync stall, backpressure propagation to incoming feed sockets.
- **Remediation**: Check for conflicting heavy I/O operations (e.g., ad-hoc queries, full SQLite VACUUM).

---

### 2.5 IngestLog Queue Depth (Internal Backpressure)
- **Rule Name**: `MDRAPIngestLogBackpressureAccumulation`
- **Severity**: `CRITICAL`
- **Condition**: IngestLog memory buffer depth exceeds 20,000 pending events.
- **PromQL**:
  ```promql
  mdrap_ingest_log_pending_events > 20000
  ```
- **Operational Impact**: Imminent event dropping or forced TCP socket throttling.
- **Remediation**: Verify `fsync_policy` configuration; switch dynamically to `grouped_by_size`.

---

### 2.6 Shared Memory (SHM) Consumer Lag
- **Rule Name**: `MDRAPConsumerLagExhaustion`
- **Severity**: `WARNING`
- **Condition**: SPSC ring buffer reader head is more than 500,000 slots behind writer head.
- **PromQL**:
  ```promql
  (mdrap_shm_writer_sequence - mdrap_shm_reader_sequence) > 524288
  ```
- **Operational Impact**: Reader risks being overrun by writer, resulting in sequence drops.
- **Remediation**: Restart lagged downstream consumer process or allocate higher priority core.

---

## 3. Alert Routing and Notification Matrix

| Alert Name | Severity | Escalation Channel | Auto-Remediation Triggered |
| :--- | :--- | :--- | :--- |
| `MDRAPHostCPUSaturation` | WARNING | `#mdrap-ops-alerts` Slack | None |
| `MDRAPMemoryLeakDetected`| CRITICAL | PagerDuty SRE On-call | Diagnostic bundle dump |
| `MDRAPDiskPartitionLowSpace` | CRITICAL | PagerDuty + Email | Run `scripts/archive.py` |
| `MDRAPDiskWriteLatencySpike` | WARNING | `#mdrap-ops-alerts` Slack | Engage group fsync |
| `MDRAPIngestLogBackpressureAccumulation` | CRITICAL | PagerDuty SRE On-call | Throttle non-critical feeds |
| `MDRAPConsumerLagExhaustion` | WARNING | Consumer Desk Alert | Disconnect slow reader |
