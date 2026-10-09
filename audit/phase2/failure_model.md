# MDRAP Phase 2 — System Failure Model & Crash Escalation Hierarchy

**Document Identifier**: `MDRAP-FAIL-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: `src/mdrap/supervisor.py`, `src/mdrap/runtime.py`  
**Test Suite**: `tests/test_phase2_supervision.py`  

---

## 1. Classification of Failures

Failures in MDRAP are strictly categorized into two classes with distinct lifecycle handling:

### 1.1 Transient Failures (Retried with Bounded Exponential Backoff)
Transient failures are expected operational anomalies that do not compromise state invariants:
- Network disconnects on upstream WebSocket/TCP market data feeds.
- DNS resolution timeouts or transient gateway resets.
- Temporary client connection resets.
- Ephemeral shared-memory reader lag.

**Policy**:
- Supervised by `RuntimeSupervisor` using bounded exponential backoff.
- Backoff formula: `delay = min(base * (2 ** retry_count), max_delay)` (defaults: base=0.2s, max=5.0s).
- Maximum retries within a sliding time window (defaults: 3 retries in 10.0 seconds).
- If retries are exhausted within the window, the failure escalates to **Fatal**.

### 1.2 Fatal Invariant Failures (Immediate Escalation, Zero Retries)
Fatal failures represent violations of core system integrity where continued operation would risk data corruption, silent data loss, or inconsistent state:
- IngestLog WAL checksum corruption or poison state (`IngestLogCorruptError`).
- SQLite storage unrecoverable disk I/O or permission failures.
- Non-monotonic sequence generation or invalid transaction commit.
- Explicit unrecoverable exceptions (`FatalWorkerError`).

**Policy**:
- Bypasses the retry budget completely.
- Worker immediately marked `FAILED_FATAL`.
- Triggers `on_fatal_failure` callback.
- Runtime immediately transitions to `FAILED`.
- Readiness probe immediately fails with HTTP 503 to evict traffic from the node.
- Avoids futile crash-restart loops that mask catastrophic failures.

---

## 2. Worker Supervision State Transitions

```
               +-------------+
               |    IDLE     |
               +------+------+
                      | start()
                      v
        +-----------> RUNNING <-----------+
        |                 |               |
        | [normal exit]   | [transient    | [restart delay
        |                 |  crash]       |  expires]
        v                 v               |
   +---------+       +---------+          |
   | STOPPED |       | CRASHED |          |
   +---------+       +----+----+          |
                          |               |
                          | [within       |
                          |  budget]      |
                          v               |
                     +------------+       |
                     | RESTARTING +-------+
                     +----+-------+
                          |
                          | [budget exhausted
                          |  OR FatalWorkerError]
                          v
                   +--------------+
                   | FAILED_FATAL |
                   +--------------+
```

---

## 3. Verification Evidence

Verified by `tests/test_phase2_supervision.py`:
- `test_supervisor_normal_worker_lifecycle`: Clean startup, telemetry tracking, and non-blocking join on shutdown.
- `test_supervisor_transient_crash_restart`: Automatic backoff retry and state recovery after transient crashes.
- `test_supervisor_fatal_worker_error_escalation`: Immediate escalation of `FatalWorkerError` with zero retry delay.
- `test_supervisor_exhausted_retries_escalation`: Escalation to `FAILED_FATAL` when crashes exceed `max_retries` within window.
