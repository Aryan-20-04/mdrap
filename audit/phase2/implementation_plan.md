# MDRAP Phase 2 — Implementation Plan: Production Runtime Architecture & Operational Hardening

**Document Identifier**: `MDRAP-PLAN-P2-001`  
**Author**: Principal Systems Engineer & Quantitative Trading Infrastructure Architect  
**Input Baseline**: Phase 0 Baseline Audit (`audit/phase0/`) & Phase 1 Exit Report (`audit/phase1/`)  
**Discipline**: `/ponytail` (stdlib first, shortest diffs, code-first proof, zero speculative bloat)  

---

## 1. Traceability: Workstreams to Requirements & Invariants

| Workstream | Requirement | Affected Files | Target Verification Test |
|---|---|---|---|
| **Workstream A: Runtime Lifecycle** | Standardize canonical runtime lifecycle (`UNINITIALIZED -> INITIALIZING -> READY -> RUNNING -> DRAINING -> STOPPED`) with idempotent shutdown and drain deadline | `src/mdrap/runtime.py`, `src/mdrap/engine.py` | `tests/test_phase2_lifecycle.py` |
| **Workstream B: Bounded Resources & Backpressure** | Audit and bound all queues (TCP clients, WebSocket subscribers, feed manager); explicit drop counters; non-blocking broadcast | `src/mdrap/service.py`, `src/mdrap/api.py`, `src/mdrap/ws_feed.py` | `tests/test_phase2_backpressure.py` |
| **Workstream C: Supervision & Failure Recovery** | Task supervision; propagate worker crashes to supervisor; bounded retries; prevent duplicate supervisors | `src/mdrap/supervisor.py` | `tests/test_phase2_supervision.py` |
| **Workstream D: Health & Observability** | Separate liveness, readiness, and degraded status derived from real component state; alert catalogue | `src/mdrap/api.py`, `src/mdrap/watchdog.py` | `tests/test_phase2_observability.py` |
| **Workstream E: Configuration Validation** | Fail closed on invalid/unknown configuration; enforce precedence (CLI > Env > File > Defaults); reject unsafe bounds | `src/mdrap/config_loader.py` | `tests/test_phase2_config_validation.py` |
| **Workstream F: Linux Deployment Hardening** | Hardened systemd unit, production Dockerfile, runtime non-root user, file permissions | `packaging/systemd/mdrap.service`, `Dockerfile` | `tests/test_phase2_deployment.py` |
| **Workstream G: Performance & Load Validation** | Measure throughput and p50/p95/p99 latency under saturated queue load and failure recovery | `benchmarks/phase2_benchmark.py` | `audit/phase2/performance_report.md` |

---

## 2. Dependency-Ordered Implementation Tasks

### Task 1: Canonical Runtime & Lifecycle Manager (`src/mdrap/runtime.py`)
- Define `RuntimeState` enum: `UNINITIALIZED`, `INITIALIZING`, `READY`, `RUNNING`, `DRAINING`, `STOPPED`, `FAILED`.
- Create `Runtime` container:
  - Manages `Engine` and attached projections.
  - Explicit start sequence: `initialize()` -> `start()`.
  - Graceful shutdown: `stop(drain_timeout_s=5.0)` -> sets `DRAINING`, flushes pending writes to WAL/projections, closes sockets/files, transitions to `STOPPED`.
  - Idempotent: multiple calls to `start()` or `stop()` are safe no-ops.

### Task 2: Bounded Resources & Backpressure Controls
- In `src/mdrap/service.py` (`MarketDataDaemon`):
  - Ensure client session queues (`_ClientSession.queue`) are bounded with explicit drop counters and client eviction on prolonged stall.
- In `src/mdrap/api.py`:
  - Enforce bounded subscriber queues with `QueueFull` counter tracking and degraded state signaling if drop rate exceeds threshold.
- In `src/mdrap/ws_feed.py`:
  - Ensure venue queues are bounded with explicit eviction logging and counter reporting.

### Task 3: Supervision & Worker Failure Propagation (`src/mdrap/supervisor.py`)
- Provide `RuntimeSupervisor` using standard library `threading` and `asyncio`:
  - Supervises feed ingester threads, SHM drainers, and TCP broadcasters.
  - Bounded restart policy (e.g. max 3 restarts in 10 seconds with exponential backoff).
  - Fatal error escalation: non-recoverable invariant violations (e.g. `IngestLogCorruptError`, permission failure) immediately transition runtime to `FAILED` and halt readiness.

### Task 4: Observability, Metrics & Alert Catalogs
- Update `/readiness` and `/health` to inspect `RuntimeState` and supervisor status.
- Document metrics catalog (`audit/phase2/metrics_catalog.md`) and alert thresholds (`audit/phase2/alert_catalog.md`).

### Task 5: Configuration Validation
- In `src/mdrap/config_loader.py`:
  - Validate numeric limits (e.g. `max_segment_bytes > 0`, `reorder_window_s >= 0`).
  - Reject unknown or misspelled keys in `mdrap.toml`.

### Task 6: Linux Packaging & Systemd Hardening
- Provide `packaging/systemd/mdrap.service` with Linux sandbox directives (`ProtectSystem=strict`, `NoNewPrivileges=true`, `PrivateTmp=true`, `User=mdrap`).
- Validate container build and execution parameters.

### Task 7: Empirical Performance & Load Benchmark
- Run `benchmarks/phase2_benchmark.py` under saturated load, recording p50, p95, p99 latencies and queue high-water marks.
