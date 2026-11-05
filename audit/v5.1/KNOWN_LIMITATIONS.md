# MDRAP v5.1 — Known Operational Limitations & Boundaries

**Author**: Principal Systems Architect & Release Auditor  
**Branch**: `v5.1-development`  
**Classification**: `[SOURCE-VERIFIED & EMPIRICALLY-PROVEN]`  
**Date**: 2026-11-05  

---

## 1. Concurrency & Scaling Boundaries

1. **Single-Process Python GIL Ceiling (~200,000 EPS)**:
   Although individual C11 atomic queues operate at $> 45\text{M ops/s}$ and the C validation kernel runs at $> 400\text{k EPS}$, Python thread orchestration within a single process experiences execution bottlenecking around 200,000 EPS across 4+ workers. Scaling beyond 200k EPS requires multi-process deployments.
2. **Channel SPMC Seqlock Invariant**:
   Shared memory ring buffers (`SHMPublisher`) are strictly Single-Producer Multi-Consumer. Multiple threads or processes must never write to the same SHM segment name. Multi-feed topologies must use dedicated channel segments (`mdrap_{channel}_v3`).
3. **Queue Sizing & Backpressure**:
   The native SPSC queue capacity must be a power of two. When full, `offer()` immediately returns `False` (backpressure). Publishers must implement bounded retry loops or record drop telemetry.

---

## 2. Platform & Networking Boundaries

1. **Kernel-Bypass Platform Restrictions**:
   Linux AF_XDP and DPDK zero-copy drivers require Linux OS with root or `CAP_NET_RAW` capabilities. On Windows and macOS, the system automatically runs on tuned standard BSD sockets (`HighSpeedSocketReceiver`) with 16 MB `SO_RCVBUF`.
2. **Windows Sleep Timer Resolution**:
   On Windows, default `time.sleep()` resolution is 1.0–15.6 ms. The high-performance channel worker uses spin-counts (10,000 spins) before yielding to prevent artificial latency inflation during queue draining.

---

## 3. Storage & Replay Boundaries

1. **WAL Segment Expiry**:
   Active segment files cannot be deleted until closed and superseded. `delete_segments_before()` will refuse to delete the active segment even if all offsets within it are before the checkpoint offset.
2. **SQLite Write Lock Contention**:
   SQLite WAL mode allows multiple readers but strictly one writer. Rebuilding projections or executing mass batch inserts must be serialized to avoid `database is locked` errors.
