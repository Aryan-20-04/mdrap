# MDRAP 6.0.0 — REMEDIATION REPORT: AGENT 4

## WORKSTREAM: CONCURRENCY, QUEUE SATURATION & WORKER LIFECYCLE
**Findings Remediated**: `MDRAP-CONC-01`, `MDRAP-CONC-02`  
**Engineer / Lead**: Agent 4 — Concurrency & High-Throughput Systems Specialist  
**Date**: 2026-11-12  
**Branch**: `remediation/v6.0.0-p1-hardening`  
**Status**: **VERIFIED FIXED**

---

### 1. Original Defect Descriptions

#### Defect 1 (`MDRAP-CONC-01`): Uncaught `queue.Full` Crash During Multi-Feed Eviction
In `src/mdrap/ws_feed.py:834-846`, when the shared event queue became saturated (`max_queue_size`), the venue worker caught `queue.Full` and popped an item with `get_nowait()`. However, the subsequent push `self._queue.put_nowait(raw)` was unguarded. In concurrent multi-producer environments (e.g. 5 crypto feeds bursting simultaneously), another thread could claim the freed slot between `get_nowait()` and `put_nowait(raw)`, triggering an uncaught `queue.Full` exception that permanently killed the connection worker thread.

#### Defect 2 (`MDRAP-CONC-02`): Cross-Thread `QueueFull` Eviction Bypass
In `src/mdrap/api.py:404-415`, `broadcast_event()` dispatched messages to subscribers across thread boundaries via `ws_loop.call_soon_threadsafe(q.put_nowait, event_data)`. When `q` was full, the `asyncio.QueueFull` exception was raised asynchronously inside the destination event loop rather than the caller's context. The local `try...except asyncio.QueueFull` never caught it. The exception was logged as an unhandled callback error, subscriber drop counters were bypassed, and unresponsive slow clients were never evicted.

---

### 2. Implementation Details & Files Changed

#### Modified: [`src/mdrap/ws_feed.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ws_feed.py)
Wrapped the eviction push in a fallback `try...except queue.Full`:
```python
try:
    self._queue.put_nowait(raw)
except queue.Full:
    # Slot taken by concurrent worker thread; drop frame safely without crash
    evicted = raw
```
This guarantees that regardless of multi-producer thread interleaving, no unhandled exception escapes `_venue_worker`.

#### Modified: [`src/mdrap/api.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/api.py)
1. Added `_enqueue_subscriber_event(self, ws, q, event_data)`:
   - Executes inside the destination event loop context (whether called directly or via `call_soon_threadsafe`).
   - Catches `asyncio.QueueFull` directly at the point of enqueue.
   - Atomically increments `self.total_subscriber_drops` and `self.subscriber_drops[ws]`.
   - Flags slow consumers exceeding `self.max_client_drops` into `self._dead_subscribers`.
2. Updated `broadcast_event()`:
   - Dispatches `self._enqueue_subscriber_event` safely across threads.
   - Reaps all flagged dead sockets, canceling their background sender tasks and closing connections with status code `1008`.

---

### 3. Regression & Verification Tests

#### Created: [`tests/test_backpressure_lifecycle.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_backpressure_lifecycle.py)
1. `test_multithreaded_queue_eviction_race_safety`:
   - Saturates a bounded queue (maxsize=5) and runs 5 concurrent producer threads (250 frames) racing to evict and put items.
   - Asserts 0 unhandled exceptions escape the threads, all threads complete cleanly, and drop counters are accurately recorded.
2. `test_cross_thread_broadcast_queue_full_safety`:
   - Saturates an `asyncio.Queue` (maxsize=2) and broadcasts 5 ticks from an external background thread.
   - Asserts `total_subscriber_drops` and `subscriber_drops[ws]` increment to $\ge 3$.
   - Confirms that exceeding `max_client_drops` flags the client into `_dead_subscribers` and cleanly closes the connection.

---

### 4. Execution Verification Evidence
```
tests/test_backpressure_lifecycle.py::test_multithreaded_queue_eviction_race_safety PASSED [ 50%]
tests/test_backpressure_lifecycle.py::test_cross_thread_broadcast_queue_full_safety PASSED [100%]

======================== 2 passed, 1 warning in 1.15s =========================
```

---

### 5. Remaining Limitations
None within single-host process queues. Network-level socket buffer backpressure (TCP zero-window) is handled by kernel TCP stack.
