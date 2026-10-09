# MDRAP Phase 2 — Backpressure & Queue Inventory

**Document Identifier**: `MDRAP-BACKPRESSURE-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: `src/mdrap/service.py`, `src/mdrap/api.py`, `src/mdrap/ws_feed.py`  
**Test Suite**: `tests/test_phase2_backpressure.py`  

---

## 1. Overview & Policy

An institutional market data platform must never allow slow consumers, stalled network sockets, or transient feed bursts to cause unbounded memory growth or stall core ingestion threads. 

### Core Backpressure Principles
1. **Never block the hot path**: Ingestion, validation, and canonical arbitration threads must execute lock-free or bounded-lock and never wait indefinitely on slow downstream consumers.
2. **Strict queue boundedness**: Every queue in the system has a strictly defined, finite `maxsize`.
3. **Explicit drop accounting**: Every dropped frame or tick must be accounted for in monotonic counters and structured log events. Silent eviction is prohibited.
4. **Stalled client eviction**: Consumers that accumulate excessive drops are evicted and disconnected to protect server file descriptors and buffer memory.
5. **Degradation transparency**: When global drop counts breach configured thresholds, the runtime automatically signals degraded status across health and monitoring endpoints.

---

## 2. Queue Inventory & Eviction Policy

| Subsystem | Location | Queue Type | Default Capacity | Full Queue Behavior | Eviction Threshold | Telemetry Counters |
|---|---|---|---|---|---|---|
| **TCP Streaming Daemon** | `src/mdrap/service.py` (`_ClientSession.queue`) | `queue.Queue` | 1,000 frames | `put_nowait` raises `queue.Full`, increments drop counter | Evicted at 1,000 dropped ticks | `sess.dropped_ticks`, `_total_dropped`, `stats()["dropped_ticks"]` |
| **WebSocket API Streaming** | `src/mdrap/api.py` (`AppState.subscriber_queues`) | `asyncio.Queue` | 1,000 frames | `put_nowait` raises `asyncio.QueueFull`, records drop counter | Evicted at 500 dropped frames (`MDRAP_MAX_CLIENT_DROPS`) | `subscriber_drops[ws]`, `total_subscriber_drops` |
| **Multi-Venue WS Feed Manager** | `src/mdrap/ws_feed.py` (`_queue`) | `queue.Queue` | 100,000 frames | Oldest frame evicted (`get_nowait`) to preserve real-time low latency; drop tombstone emitted | Continuous real-time ring; tombstone audit trail | `_drop_count`, `_drop_counts[venue]`, `stats()["_drops"]` |
| **Native Replay Ring Buffer** | `src/mdrap/service.py` (`_replay_buffer`) | `NativeReplayBuffer` (C / SHM) | 65,536 events | Monotonic seqlock overwrite (oldest overwritten) | Circular overwriting buffer | `replay_buffer["capacity"]`, `replay_buffer["count"]` |

---

## 3. Degradation Triggers

The system transitions to degraded state (`is_degraded() -> (True, reason)`) under the following conditions:
1. **TCP Streaming Daemon**: `total_dropped > 5000` or `shm_errors > 100`.
2. **WebSocket API**: `total_subscriber_drops > 1000`.
3. **Storage / Engine**: `pipeline.degraded == True` or `WAL IngestLog._is_poisoned == True`.
4. **Watchdog**: All monitored feeds are in `SILENT` state.

When degraded, `/v1/health` reports `"status": "degraded"` with `"degraded": true` and the corresponding `"degraded_reason"`.
