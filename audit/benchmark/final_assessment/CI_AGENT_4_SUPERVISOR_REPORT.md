# CI AGENT 4: Supervisor Crash and Restart State Machine Report

## Executive Summary
Agent 4 investigated and resolved the supervisor transient restart failure observed on macOS Python 3.13 (`state == RESTARTING` instead of `RUNNING`). The supervisor state machine was enhanced with condition-variable synchronization (`_state_cv`), atomic transition notifications, and a deterministic state-awaiting API (`wait_for_worker_state`), eliminating timing-dependent test races without increasing arbitrary sleep intervals.

## Original Failure
- **Platform**: macOS (Python 3.13)
- **Test**: `tests/test_phase2_supervision.py::test_supervisor_transient_crash_restart`
- **Error**: Expected `WorkerState.RUNNING.value`, observed `RESTARTING`. The worker experienced two transient crashes, but test inspection occurred before the second backoff timer fired.

## Root Cause Analysis
1. **Timing-Dependent Sleep Race vs Backoff Schedulers**:
   In `test_supervisor_transient_crash_restart`, the test performed `time.sleep(0.4)`. The flaky worker required two restarts:
   - Initial crash -> backoff delay ($0.05 \times 2^0 = 0.05\text{s}$) -> restart #1.
   - Immediate crash #1 -> backoff delay ($0.05 \times 2^1 = 0.10\text{s}$) -> restart #2.
   Under macOS runner virtual CPU contention and Python GIL scheduling, cumulative monitor loop interval (50ms) + timer #1 (50ms) + monitor loop interval #2 (50ms) + timer #2 (100ms) exceeded 400ms. When `supervisor.get_status()` was called at 400ms, timer #2 was still pending, leaving `info.state` in `WorkerState.RESTARTING`.
2. **Lack of Observable State Transition Signaling**:
   `RuntimeSupervisor` previously lacked condition variable notification. External callers had no mechanism to await a specific worker state transition deterministically.

## Design & Implementation
1. **Supervisor State Machine Condition Variable (`_state_cv`)**:
   - Added `self._state_cv = threading.Condition(self._lock)` in `RuntimeSupervisor.__init__`.
   - Every state mutation (`RUNNING`, `CRASHED`, `RESTARTING`, `STOPPED`, `FAILED_FATAL`) acquires `self._lock` and triggers `self._state_cv.notify_all()`.
2. **Deterministic State Awaiting API (`wait_for_worker_state`)**:
   - Implemented `wait_for_worker_state(worker_name: str, target_state: WorkerState, min_restarts: int = 0, timeout_s: float = 3.0) -> bool`.
   - Allows tests and callers to block until a worker enters the target state with at least `min_restarts`, preventing premature returns on initial worker startup.
3. **Clean Restart Execution & Shutdown**:
   - Verified that `do_restart()` transitions atomically and that `stop()` cancels pending timers cleanly without deadlocks.

## Regression Test Coverage
- `test_supervisor_transient_crash_restart`: Synchronized with `wait_for_worker_state("flaky", WorkerState.RUNNING, min_restarts=2, timeout_s=3.0)`.
- `test_supervisor_deterministic_state_waiting`: Verifies condition variable response to rapid transitions, timeout expiry, and clean shutdown.
- `test_supervisor_shutdown_during_restart_delay`: Proves that terminating the supervisor while a worker is in backoff does not hang or deadlock.

## Verification Evidence
Ran: `pytest tests/test_phase2_supervision.py -v`
Result: **6 passed, 0 failed**.
