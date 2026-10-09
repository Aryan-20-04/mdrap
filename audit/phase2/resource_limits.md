# MDRAP Phase 2 — System Resource Limits & Bounded Memory Profile

**Document Identifier**: `MDRAP-RESOURCE-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: Memory, Sockets, Ring Buffers, Storage  

---

## 1. Resource Allocations and Bounded Limits

MDRAP enforces deterministic upper bounds on memory and OS resource consumption:

| Resource Dimension | Config / Constant | Bounded Upper Limit | Failure / Enforcement Action |
|---|---|---|---|
| **Max Frame Payload Size** | `MAX_FRAME_LENGTH` | 16 MB (16,777,216 bytes) | Hard rejection with `FrameSizeExceededError`; closes network socket |
| **TCP Client Queue** | `_ClientSession.queue` | 1,000 messages | Non-blocking drop; client evicted upon 1,000 drops |
| **WebSocket Subscriber Queue** | `AppState.subscriber_queues` | 1,000 messages | Non-blocking drop; client evicted upon 500 drops (`MDRAP_MAX_CLIENT_DROPS`) |
| **Venue Ingest Buffer** | `WebSocketFeedManager._queue` | 100,000 frames (~25 MB) | Circular ring eviction of oldest frame with drop tombstone callback |
| **In-Memory Replay Buffer** | `NativeReplayBuffer` | 65,536 events (~16 MB) | Bounded circular ring buffer with seqlock wrapping |
| **WAL IngestLog Segment Size** | `segment_size_bytes` | 64 MB (configurable) | Rotates to next segment; index maintains memory-mapped bounds |
| **Token Bucket Rate Limiter** | `TokenBucketRateLimiter` | Per-client burst ceiling (e.g. 50,000) | Drops / rejects excessive client requests with HTTP 429 |
| **TCP Socket Listen Backlog** | `listen(64)` | 64 pending connections | OS rejects excess connections with TCP RST |
| **Active Client Connections** | `max_clients` | Recommended 256 concurrent clients | Monitored by supervisor and watchdog |

---

## 2. Memory Footprint Under Maximum Load

Under saturated load with 100 connected TCP clients and 100 connected WebSocket clients:
- **Client Queue Memory**: 200 clients × 1,000 frames × 256 bytes ≈ 51.2 MB.
- **Replay Buffer**: 65,536 events × 256 bytes ≈ 16.8 MB.
- **Venue Ingest Queue**: 100,000 raw frames × 256 bytes ≈ 25.6 MB.
- **Working Set (Base Engine + Python Runtime)**: ~80 MB.
- **Total Working Set Under Saturated Load**: < 200 MB RSS.

This deterministic profile ensures MDRAP runs safely in production environments with container memory limits as low as 512 MB without risk of Linux OOM killer invocation.
