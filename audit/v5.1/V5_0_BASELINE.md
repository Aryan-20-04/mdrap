# MDRAP v5.0 — Authoritative Performance & Architectural Baseline

**Reference Revision**: Commit `6bdd3ea`  
**Evaluation Date**: 2026-11-05  
**Document**: `audit/v5.1/V5_0_BASELINE.md`  
**Classification**: `[BASELINE-SPECIFICATION]`

---

## 1. Baseline Summary

MDRAP v5.0 is the foundational release upon which the v5.1 high-performance architecture is constructed.

### Core Architectural Contracts
- **Validation Monotonicity**: `INVALID > SUSPICIOUS > VALID` strictly enforced across 15 canonical quality rules.
- **Dual-Path Parity**: Pure Python fallback engine matches native C fastpath bit-for-bit across all status codes and reason masks.
- **Persistence Guarantees**: Truthful durability contracts (`PHYSICAL_FSYNC_DURABLE`, `GROUPED_TIME_FSYNC_DURABLE`, `OS_BUFFERED_NO_POWER_LOSS_PROTECTION`). Zero acknowledged event loss confirmed across 25 randomized kill-9 crash recovery tests.
- **IPC Distribution**: Shared Memory v3 with two-phase commit lock-free SPMC seqlock delivering sub-6 $\mu$s tail latency on co-located Linux and Windows environments.

---

## 2. Empirical Performance Profile (682,000 Event Suite)

```text
========================================================================================
 MDRAP v5.0 Empirical 12-Tier Performance Scoreboard
 Host: Intel Core i7-1255U (10C/12T), 15.7 GB DDR4, NVMe SSD, Win11 AMD64
========================================================================================
 Tier 01: Native CRC32C (1 KB)           : 425,267.0 ops/sec (0.41 - 0.45 GB/s) | p50:  1.90 us
 Tier 02: Native Batch Processing (128)  : 271,182.2 - 414,710.1 EPS            | p50:  3.48 us
 Tier 03: Python/C FFI Crossing (Single) : 312,817.1 - 472,777.9 EPS            | p50:  2.60 us
 Tier 04: ITCH 5.0 Wire Parser           : 250,539.3 - 400,779.4 EPS            | p50:  3.40 us
 Tier 05: SBE Wire Decoding              : 303,683.6 - 418,263.2 EPS            | p50:  2.80 us
 Tier 06: SPSC Lock-Free Queue           : 1,235,403.7 - 1,689,446.0 EPS        | p50:  0.20 us
 Tier 07: In-Memory Engine Submit        : 27,168.9 - 29,223.9 EPS              | p50: 32.90 us
 Tier 08: WAL Append (Buffered Log)      : 63,275.6 - 69,601.6 EPS              | p50: 13.40 us
 Tier 09: WAL Immediate Fsync            : 261.6 - 290.2 EPS                    | p50: 3.26 ms
 Tier 10: SQLite Projections (Async)     : 78,840.2 - 92,657.5 EPS              | p50:  1.90 us
 Tier 11: SHM v3 IPC Delivery            : 172,636.9 - 174,981.0 EPS            | p50:  5.10 us
 Tier 12: Full End-to-End Pipeline       : 14,000.6 EPS                         | p50: 58.80 us (p99: 198.3 us)
 Release Gate 13: Regression Guard       : 47,140.6 EPS                         | p50: 17.00 us (p99:  87.3 us)
========================================================================================
```

---

## 3. Top Bottlenecks for v5.1 Remediation

1. **Top-Level Orchestration Overhead**: `Engine.submit()` in Python caps single-feed throughput to $\sim 70,000\text{ EPS}$.
2. **Concurrency Safety in Free-Threaded Python**: `SpscRingQueue` requires C11 memory barriers for PEP 703 compatibility.
3. **Multi-Core Scaling Gap**: Single-threaded event dispatch limits platform scalability across multi-core systems.
4. **SQLite Checkpoint Jitter**: Occasional 14 ms latency outliers during transaction checkpointing.
