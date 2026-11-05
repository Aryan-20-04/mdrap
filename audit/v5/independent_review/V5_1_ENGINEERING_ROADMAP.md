# MDRAP v5.1 — Prioritized Engineering Roadmap & Technical Specifications

**Document Version**: 5.1.0-DRAFT  
**Author**: Principal Low-Latency Systems Architect  
**Planning Horizon**: Q1–Q2 2027  
**Classification**: `[ROADMAP-SPECIFICATION]`

---

## 1. Architectural Mission for v5.1

The v5.0 audit demonstrated that MDRAP achieved reliable validation, fault tolerance, and solid tier-2 throughput (47k–69k EPS). The primary engineering mandate for **MDRAP v5.1** is to:
1. **Harden Concurrency for Modern Python**: Implement native C memory fences for `SpscRingQueue` to achieve full safety under free-threaded Python 3.13t (PEP 703 nogil).
2. **Scale Multi-Feed Throughput**: Transition from single-threaded Python event loops to a multi-shard architecture with dedicated C-accelerated ingress rings.
3. **Deepen Native C Kernel Capabilities**: Implement SIMD AVX2 vectorization for multi-instrument statistical calculations and direct zero-copy byte framing.

---

## 2. Prioritized Roadmap Phases

```text
Phase A: Correctness & Memory Hardening  ──►  Phase B: Hot-Path Optimization
                     │                                        │
                     ▼                                        ▼
Phase C: Multi-Core Sharded Concurrency ──►  Phase D: Native Acceleration (SIMD/FFI)
                     │                                        │
                     ▼                                        ▼
Phase E: Storage & Group Commit Tuning  ──►  Phase F: Institutional Protocol Integration
```

---

### Phase A — Correctness, Memory Barriers & Concurrency Hardening (Priority: Critical)
- **Objective**: Eliminate all GIL dependencies and guarantee 100% thread safety across free-threaded CPython and weakly-ordered architectures (ARM64).
- **Work Items**:
  1. **Native C SPSC Ring Buffer (`src/mdrap/_fastpath_spsc.c`)**: Implement circular ring queue with explicit `__atomic_store_n(head, __ATOMIC_RELEASE)` and `__atomic_load_n(head, __ATOMIC_ACQUIRE)` primitives.
  2. **Multi-Platform Memory Ordering CI**: Add continuous integration matrix running tests under `python3.13t` (`--disable-gil`) on Linux x86-64 and ARM64.
- **Acceptance Criteria**:
  - `pytest tests/ -v` passes cleanly under free-threaded Python 3.13t with GIL disabled.
  - SPSC stress test transfers 100,000,000 events without dropped elements or torn slot reads under high contention.

---

### Phase B — Hot-Path Micro-Optimization & Timing Accuracy (Priority: High)
- **Objective**: Remove remaining micro-allocations on the critical path and modernize microbenchmark instrumentation.
- **Work Items**:
  1. **Direct Pre-Serialized Byte Framing in WAL**: If `RawEvent.wire` is already bytes, write directly to segment file without wrapping or decoding into a Python dict.
  2. **Batch-Amortized Timing in Microbenchmarks**: Replace per-tick `perf_counter_ns()` calls in microbenchmarks with 1,000-iteration block timing to eliminate observer effect.
- **Acceptance Criteria**:
  - Microbenchmark measurement overhead reduced from 50 ns to $< 2\text{ ns}$ per event.
  - Buffered WAL append throughput increases from 69,600 EPS to **$\ge$ 85,000 EPS**.

---

### Phase C — Multi-Core Sharded Topologies & Scaling (Priority: High)
- **Objective**: Scale aggregate platform throughput linearly across CPU cores using dedicated feed shards.
- **Work Items**:
  1. **Multi-Shard Fleet Coordinator**: Integrate `ChannelIngressSequencer` with worker processes pinned to specific CPU cores via `sched_setaffinity` / Win32 core affinity.
  2. **Dedicated Channel SHM Rings**: Allocate distinct, isolated SHM segments per venue feed (`mdrap_binance_v3`, `mdrap_nasdaq_v3`), eliminating cross-venue writer contention.
- **Acceptance Criteria**:
  - Multi-shard throughput scales to **$\ge$ 200,000 EPS** aggregate across 4 CPU cores.
  - Zero cross-shard lock contention or head-of-line blocking.

---

### Phase D — Native C Acceleration & SIMD Vectorization (Priority: Medium)
- **Objective**: Exploit modern CPU vector units for batch validation and statistical calculations.
- **Work Items**:
  1. **AVX2 Vectorized Welford Volatility**: Vectorize rolling mean and variance calculations across 8 instruments simultaneously using 256-bit AVX2 registers (`_mm256_add_pd`, `_mm256_mul_pd`).
  2. **Branchless Crossed-Quote Detection**: Replace conditional branches with bitwise operations in native C batch loops.
- **Acceptance Criteria**:
  - Native batch processing throughput increases from 414k EPS to **$\ge$ 750,000 EPS**.
  - Dual-path differential parity tests remain 100% green across all seeds.

---

### Phase E — Storage & Durability Engine Refinements (Priority: Medium)
- **Objective**: Improve sustained disk persistence during high-volume market volatility bursts.
- **Work Items**:
  1. **Adaptive Group Commit Tuning**: Dynamically modulate `group_commit_interval_ms` based on instantaneous ingestion velocity (shortening to 2 ms during bursts, relaxing to 50 ms during quiet periods).
  2. **SQLite Autocheckpoint Boundary Tuning**: Tune `PRAGMA wal_autocheckpoint = 25000` (100 MB segments) to prevent synchronous checkpoint pauses during trading market open/close.
- **Acceptance Criteria**:
  - Full pipeline p99 latency during heavy bursts remains $< 150\text{ }\mu\text{s}$.
  - Zero uncheckpointed WAL bloat beyond configured 100 MB caps.

---

### Phase F — Institutional Market Connectivity & Networking (Priority: Low / Future)
- **Objective**: Bridge the gap between software simulation and live exchange colocation.
- **Work Items**:
  1. **Live Linux AF_XDP Driver**: Implement production C driver linking against `libbpf` for zero-copy Linux kernel-bypass network packet reception.
  2. **CME SReP TCP Client**: Build automated TCP client for CME Group historical gap fill and snapshot recovery.
- **Acceptance Criteria**:
  - Live AF_XDP driver ingests raw network packets at $\ge 1,000,000\text{ packets/sec}$ on Linux testbed with zero kernel packet drops.
