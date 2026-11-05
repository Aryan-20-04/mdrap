# MDRAP v5.0 Top 10 Performance Bottlenecks

## 1. Executive Summary

Based on cProfile execution profiling, memory tracking, and source inspection on `v5.0-development` (commit `4ee4825`), this document identifies and ranks the ten largest performance bottlenecks on the critical market-data ingestion path.

---

## 2. Top 10 Bottlenecks Table

| # | Bottleneck | Source Location | Measured Cost | Proposed Optimization | Expected Benefit | Risk |
|:---:|:---|:---|:---:|:---|:---:|:---:|
| **1** | **Redundant JSON Serialization in IngestLog** | `src/mdrap/ingestlog.py:961` | **20.9% CPU** (0.220s / 10k ticks) | Cache or preserve pre-serialized byte payloads; avoid calling `json.dumps()` if payload is already UTF-8 bytes or SBE frame. | **+20–25% throughput** | Low |
| **2** | **Unconditional Stream Flush in IngestLog** | `src/mdrap/ingestlog.py:999` | **8.5% CPU** (0.089s, 10,001 calls) | When `fsync_policy="never"`, allow stdio 64 KB buffer to fill naturally rather than forcing `f.flush()` per tick. | **+8–10% throughput**, lower CPU | Low |
| **3** | **Repetitive OS File Pointer Inquiries (`f.tell()`)** | `src/mdrap/ingestlog.py:980, 997` | **5.3% CPU** (0.056s, 20,000 calls) | Maintain segment byte offset in memory (`self._current_segment_bytes += len(batch_buffer)`) instead of querying OS file pointer. | **+5% throughput**, zero syscalls | Negligible |
| **4** | **Redundant Serialization in `estimate_payload_size`** | `src/mdrap/engine.py:86` | **5.6% CPU** (0.059s / 10k ticks) | Use pre-calculated length attribute on `RawEvent` or fast heuristic bounds without invoking `json.dumps()`. | **+4–5% throughput** | Low |
| **5** | **Dynamic String Formatting for Stream Keys** | `src/mdrap/engine.py:1955` | **1.5% CPU** (~15 ms / 10k ticks) | Expand `_STREAM_KEY_CACHE` to cover all active symbols and venues, avoiding any string concatenation. | **+1–2% throughput**, zero string churn | Negligible |
| **6** | **Repeated `isinstance()` and Type Checks** | `src/mdrap/engine.py:1675`, `ingestlog.py:941` | **2.2% CPU** (0.024s, 110k calls) | Fast-path assume conforming `RawEvent` / `CanonicalEvent` in hot loop, deferring full type inspection to boundary validation. | **+2–3% throughput** | Low |
| **7** | **Single-Event Projection Dispatch Locking** | `src/mdrap/projection.py:124` | **3.1% CPU** (0.033s / 10k ticks) | Batch enqueue multiple items under a single `_queue_lock` acquisition in `Engine.submit_batch()`. | **+3–5% batch throughput** | Low |
| **8** | **Python/C ctypes Marshaling Overhead** | `src/mdrap/fastpath.py:1366` | **1.9 $\mu$s per call** (441k EPS) | Direct C-extension module (`_fastpath_c.pyd`) native fast call (`METH_FASTCALL`) bypassing ctypes marshaling. | **2–3x FFI speedup** | Moderate |
| **9** | **Synchronous SQLite Checkpointing Cadence** | `src/mdrap/projection.py:433` | **14.1 ms flush stalls** | Tune `PRAGMA wal_autocheckpoint = 25000` (100 MB segments) to reduce SQLite checkpoint frequency during high-volume bursts. | **-40% p99.9 latency** | Low |
| **10** | **Shared Memory Slot ASCII Decode Churn** | `src/mdrap/shm.py:171` | **0.8 $\mu$s per read** | Use integer asset IDs (u32 security IDs) rather than 16-byte ASCII strings in internal SHM ring slots. | **+15% SHM read throughput** | Low |

---

## 3. Ranked Optimization Strategy for Phase 5

The optimizations above are ranked in order of return-on-investment (ROI):
1. **Tier A (Highest Value, Lowest Risk)**:
   - Eliminate redundant `f.flush()` when `fsync_policy == "never"` (Bottleneck #2).
   - In-memory segment byte tracking to eliminate `f.tell()` syscalls (Bottleneck #3).
   - Payload byte preservation to avoid redundant `json.dumps()` in `IngestLog` (Bottleneck #1).
2. **Tier B (Medium Value, Low Risk)**:
   - Heuristic payload size estimation avoiding fallback `json.dumps()` (Bottleneck #4).
   - Full symbol/venue pair interning (Bottleneck #5).
   - Micro-batched queue enqueuing (Bottleneck #7).
3. **Tier C (Architecture Refinements)**:
   - Native C-extension C-API direct bindings bypassing ctypes (Bottleneck #8).
   - SQLite autocheckpoint tuning for heavy bursts (Bottleneck #9).
