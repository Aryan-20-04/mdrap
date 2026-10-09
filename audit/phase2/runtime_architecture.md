# MDRAP Phase 2 — Runtime Architecture Specification

**Document Identifier**: `MDRAP-ARCH-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: `src/mdrap/runtime.py`, `src/runtime.py`  
**Test Suite**: `tests/test_phase2_lifecycle.py`  

---

## 1. Overview & Motivation

Prior to Phase 2, MDRAP relied on fragmented entry points across `cli.py`, `service.py`, `pipeline.py`, and `api.py`. There was no single canonical runtime orchestrator managing the full lifecycle of the `Engine`, persistence subsystems (WAL, SQLite, Projections), and worker tasks. This led to:
- Inconsistent startup sequences and potential partial resource leaks on startup failure.
- Ungraceful shutdown paths where pending events in memory were truncated without a deterministic drain deadline.
- Ambiguous runtime states (unclear whether the system was running, ready, degraded, or shutting down).

Phase 2 Workstream A establishes `Runtime` in `src/mdrap/runtime.py` as the canonical lifecycle orchestrator.

---

## 2. Finite State Machine (FSM)

The runtime lifecycle is governed by an explicit finite state machine with strict monotonic transitions:

```
                  +-------------------+
                  |   UNINITIALIZED   |
                  +---------+---------+
                            | initialize()
                            v
                  +-------------------+
                  |   INITIALIZING    |
                  +----+---------+----+
    [Init Error]       |         |  [Success]
         +-------------+         +-------------+
         v                                     v
+-----------------+                   +-----------------+
|     FAILED      |                   |      READY      |
+-----------------+                   +--------+--------+
                                               | start()
                                               v
                                      +-----------------+
                                      |     RUNNING     |
                                      +--------+--------+
                                               | stop()
                                               v
                                      +-----------------+
                                      |    DRAINING     |
                                      +--------+--------+
                                               | (drain done or deadline)
                                               v
                                      +-----------------+
                                      |     STOPPED     |
                                      +-----------------+
```

### State Definitions
1. **`UNINITIALIZED`**: Initial state before configuration validation and resource binding.
2. **`INITIALIZING`**: Allocating storage directories, opening the WAL/`IngestLog`, loading recovery state, and initializing projections.
3. **`READY`**: Fully initialized, recovery completed, projections caught up, ready to accept ingest traffic. Not yet running live background workers.
4. **`RUNNING`**: Active ingest, background workers running, processing events.
5. **`DRAINING`**: Ingest rejected or paused, inflight queues and ring buffers draining to WAL and projections before process exit.
6. **`STOPPED`**: All resources, file descriptors, and worker threads cleanly closed and joined.
7. **`FAILED`**: Terminal state entered upon unrecoverable error during initialization or runtime panic.

---

## 3. Key Invariants & Guarantees

### 3.1 Idempotency
- Calling `initialize()` when already in `READY` or `RUNNING` is an idempotent no-op.
- Calling `start()` when already `RUNNING` is an idempotent no-op.
- Calling `stop()` when already `STOPPED` is an idempotent no-op.

### 3.2 Partial Initialization Cleanup
If an exception occurs during `initialize()` (e.g. storage directory unwritable, corrupt snapshot, database lock failure):
1. Any partially allocated `Engine` or projection handles are immediately closed.
2. The runtime transitions directly to `FAILED`.
3. The underlying exception is raised to the caller.
4. Leaked file descriptors and zombie SQLite connections are prevented.

### 3.3 Graceful Drain with Timeout
During `stop(drain_timeout_s)`:
1. The state immediately shifts to `DRAINING`.
2. Pending events in projections and WAL are flushed.
3. If draining completes within the deadline, `drain_success` is recorded as `True`.
4. If the deadline expires, the runtime logs a warning, forces resource closure, records `drain_success = False`, and transitions to `STOPPED`.

### 3.4 Runtime Observability
The `Runtime` exposes:
- `is_ready()`: `True` only when in `READY` or `RUNNING`.
- `is_alive()`: `True` in `INITIALIZING`, `READY`, `RUNNING`, or `DRAINING`.
- `uptime_seconds()`: Time elapsed since successful transition to `RUNNING`.
- `metrics()`: Dictionary containing state, uptime, engine sequence, durability counters, and projection metrics.

---

## 4. Verification Evidence

The implementation is verified by `tests/test_phase2_lifecycle.py`:
- `test_runtime_lifecycle_full_happy_path`: Validates `UNINITIALIZED -> INITIALIZING -> READY -> RUNNING -> DRAINING -> STOPPED`, idempotency of `start()` and `stop()`, and event processing.
- `test_runtime_initialization_failure_cleans_up`: Validates that simulated failure during `initialize()` closes any partially created resources, enters `FAILED`, and leaves zero dangling handles.
