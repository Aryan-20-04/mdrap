# MDRAP Phase 2 — Observability & Health Invariants Contract

**Document Identifier**: `MDRAP-OBS-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: `src/mdrap/api.py`, `src/mdrap/runtime.py`, `src/mdrap/supervisor.py`  
**Test Suite**: `tests/test_phase2_observability.py`  

---

## 1. Liveness, Readiness, and Health Triad

Institutional infrastructure orchestrators (e.g. Kubernetes, systemd, Nomad) require distinct semantics for process liveness, load-balancer traffic readiness, and operator diagnostic health.

```
+---------------+     +---------------+     +---------------+
|   /liveness   |     |  /readiness   |     |    /health    |
+-------+-------+     +-------+-------+     +-------+-------+
        |                     |                     |
        v                     v                     v
Process running?       Ready to ingest?      Detailed internal
(Restart if dead)      (Route traffic if 200) subsystem status
```

### 1.1 `/v1/liveness`
- **Purpose**: Process liveness probe. Tells the orchestrator whether the Python process is alive and responsive.
- **HTTP Code**: Always returns `200 OK` while process loop runs.
- **Response**: `{"status": "alive", "uptime_seconds": 123.45}`.
- **Failure Consequence**: Orchestrator kills and restarts container/service.

### 1.2 `/v1/readiness` (and `/v1/ready`)
- **Purpose**: Ingress traffic routing probe. Indicates whether the node can safely accept and process market data without loss or failure.
- **HTTP Code**: 
  - `200 OK`: Node is fully ready; database connected, WAL unpoisoned, projections operational.
  - `503 Service Unavailable`: Node is unready; traffic MUST NOT be routed.
- **Failure Conditions**:
  - `init_error` present (startup incomplete).
  - SQLite database connection unreachable.
  - Engine WAL `IngestLog` is poisoned (`_is_poisoned == True`).
  - Pipeline storage writer is degraded with failed dead-letter.
- **Response (200)**: `{"status": "ready", "database": "connected", "persistence_mode": "production_durable"}`.
- **Response (503)**: `{"detail": {"status": "not_ready", "reason": "<explicit root cause>"}}`.

### 1.3 `/v1/health`
- **Purpose**: Diagnostic deep-health inspection for SREs, monitoring dashboards, and alerting systems.
- **HTTP Code**: Always `200 OK` (so monitoring can inspect diagnostic payload).
- **Status Enum**:
  - `"healthy"`: All systems green, zero degradation.
  - `"degraded"`: Running, but experiencing elevated queue drops, high SHM write errors, or silent feeds.
  - `"unhealthy"`: Critical subsystem failure or uninitialized storage.
- **Fields**:
  - `status`: `"healthy" | "degraded" | "unhealthy"`.
  - `version`: SemVer string.
  - `uptime_seconds`: Process uptime.
  - `engine`: `"running" | "failed"`.
  - `active_feeds_count`: Number of active feed sources.
  - `db`: Database path and WAL mode confirmation.
  - `shm`: Shared memory publisher status.
  - `watchdog`: Healthy sources vs total monitored.
  - `degraded`: Boolean flag.
  - `degraded_reason`: Human-readable explanation of why degradation was triggered.
