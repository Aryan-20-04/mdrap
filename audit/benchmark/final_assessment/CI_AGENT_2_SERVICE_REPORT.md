# MDRAP CI Cross-Platform Remediation — Agent 2 Report: Ubuntu Daemon Broadcast and Lifecycle Reliability

## 1. Domain Ownership & Executive Summary
- **Agent Role**: Linux Daemon Broadcast, Thread Synchronization & Lifecycle Resilience
- **Primary Files**:
  - `src/mdrap/service.py`
  - `tests/test_service_resilience.py`
- **Status**: PASSED / VERIFIED (5 passed, 0 failed in 1.26s)

## 2. Issues Diagnosed & Root Cause Analysis

### Issue 1: Unthrottled Simulator Loop Starving GIL and Flooding Queues on Ubuntu Linux
- **Symptom**:
  - `test_daemon_client_connect_and_broadcast`: `TimeoutError: timed out`
  - `test_daemon_client_lifecycle_and_synchronization`: Expected 5 messages, received 1.
- **Root Cause**:
  In `MarketDataDaemon._sim_loop`, pacing was configured as:
  ```python
  sleep_s = (1.0 / self.sim_speed_eps) if (self.sim_speed_eps > 0 and self.sim_speed_eps < 1000.0) else 0.0
  ```
  When tests requested `sim_speed_eps = 5000.0` or `2000.0`, `sleep_s` became `0.0`. The simulation loop executed in an unthrottled spin loop at >100,000 eps:
  1. On Linux (Ubuntu), CPU starvation and Python GIL lock retention prevented the socket accept thread and client command reader thread from processing `AUTH` and `SUB` in time, triggering socket read `TimeoutError`.
  2. Once subscribed, the unthrottled generator flooded the client session queue (`maxsize=1000`). Within milliseconds, `dropped_ticks` exceeded `max_dropped_ticks = 1000`, causing the daemon to evict and disconnect the client after sending only 1 message.
- **Remediation**:
  1. Implemented burst-pacing in `_sim_loop`: for `sim_speed_eps >= 1000.0`, it yields a batch of `burst_size = max(1, int(self.sim_speed_eps / 1000.0))` events before sleeping 1ms (`0.001s`). This yields CPU and GIL slices to the client reader/writer threads.
  2. Increased `_ClientSession.max_dropped_ticks` from 1,000 to 20,000 and queue capacity to 2,000 to provide headroom for transient socket write bursts.
  3. Cleaned up redundant exception blocks in `StreamClient.stream()`.

## 3. Verification Evidence
Executed:
`pytest tests/test_service_resilience.py -v`
Result: **5 passed, 0 failed in 1.26s**.
