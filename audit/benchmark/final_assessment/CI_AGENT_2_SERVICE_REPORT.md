# CI AGENT 2: Linux Daemon Client and Broadcast Resilience Report

## Executive Summary
Agent 2 investigated and resolved the Ubuntu Linux broadcast stream failure (`assert 0 == 3`), established deterministic daemon lifecycle readiness synchronization, eliminated socket buffer partition race conditions in `StreamClient`, and made daemon start/stop cycles fully idempotent.

## Original Failure
- **Platform**: Ubuntu Linux (Python 3.12)
- **Test**: `tests/test_service_resilience.py::test_daemon_client_connect_and_broadcast`
- **Error**: Expected three broadcast messages, received zero (`assert 0 == 3`).

## Root Cause Analysis
1. **Socket Buffer Truncation Race in `StreamClient.stream`**:
   The legacy `StreamClient.stream` method attempted to consume the initial `SUB` subscription acknowledgment frame by waiting for a newline:
   ```python
   while b"\n" not in raw_buf:
       chunk = self.sock.recv(4096)
   _, _, rest = raw_buf.partition(b"\n")
   raw_buf = bytearray(rest)
   ```
   When the daemon was operating at high throughput (5,000 eps), incoming TCP chunks frequently contained both the `SUB` acknowledgment frame AND the first batch of broadcast `TICK` messages, or ticks arrived before the SUB frame was processed by the reader. Blindly partitioning the buffer and discarding the first element without payload inspection caused early broadcast ticks to be discarded or truncated.
2. **Missing Deterministic Readiness Synchronization**:
   The test previously used arbitrary `time.sleep(0.3)` to await daemon background thread startup. Under CI runner CPU scheduling pressure, socket listener registration and simulator thread initialization were subject to scheduling jitter.
3. **Daemon Lifecycle Idempotency**:
   Successive `stop()` and `start()` invocations on the same daemon instance left `self.pipeline` and `self.store` uninitialized or closed.

## Design & Implementation
1. **Continuous Frame Parsing in `StreamClient.stream`**:
   Refactored the streaming consumer in `src/mdrap/service.py` into a continuous buffer parser. The client no longer partitions and discards buffer heads; instead, it parses every newline-delimited JSON frame:
   - Recognizes and consumes `{"status": "OK", "action": "SUB", ...}` without dropping preceding or interleaved market ticks.
   - Preserves all incoming `TICK` payloads into the generator yield queue.
2. **Observable Daemon Readiness (`wait_ready`)**:
   - Added `_ready_event = threading.Event()` to `MarketDataDaemon`.
   - Set atomically once socket binding, socket listening, listener thread, and ingestion sequencer threads have actively started.
   - Exposed `daemon.wait_ready(timeout=5.0) -> bool` for deterministic test and application synchronization without heuristic sleeps.
3. **Idempotent Lifecycle Management**:
   - Ensured `MarketDataDaemon.start()` reinstantiates storage and pipeline engines if previously closed.
   - Ensured `MarketDataDaemon.stop()` safely nulls and unregisters resources without double-closing connections.

## Regression Test Coverage
- `test_daemon_client_connect_and_broadcast`: Validated deterministic readiness sync and 3-tick subscription limit.
- `test_daemon_client_lifecycle_and_synchronization`: Validated repeated start/stop cycles, client connection before/after readiness, sequential streaming, client disconnect during broadcast, and graceful daemon shutdown with active connected client.

## Verification Evidence
Ran: `pytest tests/test_service_resilience.py -v`
Result: **5 passed, 0 failed**.
