# MDRAP v4.0.1 — Production Operating Boundaries & Remaining Limitations

**Audit Date:** 2026-11-05  
**Auditor:** Principal Systems Engineer & Independent Release Auditor  
**Repository Baseline Commit:** `b88c3b3`  

---

## 1. Scope & Purpose

This document provides institutional operators, quantitative trading desks, and site reliability engineers with an unsparing, truthful accounting of the architectural boundaries, hardware constraints, and operational limitations of MDRAP v4.0.1.

MDRAP is designed as an institutional **market-data validation, normalization, and reconciliation sidecar** sitting immediately before trading strategies and analytics. Understanding its precise boundaries ensures safe production deployment.

---

## 2. Persistence & Durability Boundaries

### A. Sudden Process Crash vs. Physical Power Loss
- **Process Crash (`TerminateProcess` / `SIGKILL`)**:
  When the MDRAP host process is killed abruptly, the operating system kernel remains active. The OS kernel flushes dirty pages in the page cache to non-volatile storage upon file handle closure. In all tested scenarios, WAL frames and projection databases survived process crashes without corruption.
- **Physical Power Loss (Server Power Outage / Host Kernel Panic)**:
  Dirty pages residing in the OS page cache that have not been flushed via `FlushFileBuffers()` / `fsync()` will vanish upon power severance.
  - For applications requiring **zero data loss across power cuts**, MDRAP must be operated with `fsync_policy="always"` or `fsync_policy="batch"` (`grouped_by_time`).
  - Running with `fsync_policy="never"` achieves maximum I/O throughput by relying entirely on OS page cache write-back, but explicitly carries the truthful contract `OS_BUFFERED_NO_POWER_LOSS_PROTECTION`.

### B. SQLite Disk Projection Throughput Ceiling
- While the pure C validation kernel achieves computational throughput exceeding **40,000,000 ticks/sec**, and the Python in-memory engine achieves **>75,000 EPS**, the synchronous disk-backed SQLite projection pipeline is bound by relational database transactional serialization:
  - SQLite projection throughput caps at **~4,500–5,000 EPS** per worker on standard NVMe drives.
  - **Operational Recommendation**: Ultra-high-throughput algorithmic trading strategies should consume directly from the zero-copy Shared Memory (SHM) ring buffer or the sequential binary WAL stream, treating SQLite projections as an asynchronous audit and analytical store.

---

## 3. Market Data Sequencing & Deduplication Boundaries

### A. Stale Deduplication Circular Window (32 Entries)
- The platform uses a dual-tier sequence tracking engine:
  1. A 64-bit sliding bitmap for packets within 64 sequence positions of the current watermark ($[seq_{last} - 63, seq_{last}]$).
  2. A 32-entry circular buffer (`stale_seen`) for out-of-order packets arriving $>64$ steps in the past.
- **Boundary Behavior**:
  If a catastrophic network storm delivers **more than 32 distinct stale packets**, the oldest entry in the ring is evicted. If that evicted sequence number is retransmitted later, it will be classified as `SUSPICIOUS` (`REASON_OUT_OF_ORDER`) rather than `INVALID` (`REASON_DUPLICATE`).
- **Design Rationale**: This bounded memory model prevents unbounded hash table allocations and L1 cache eviction during sustained retransmission storms.

### B. Unsequenced Tick Deduplication
- Feeds lacking hardware or exchange sequence numbers (such as aggregate crypto REST/WebSocket feeds) are deduplicated via a 64-bit seeded hash of `(source_id, instrument_id, event_type, price, quantity, exchange_ts)`.
- Hash collisions across different ticks are theoretically bounded to $1 \text{ in } 2^{64}$.
- Deduplication is strictly isolated by source epoch: establishing a new session (`Reason.EPOCH_RESET`) clears the hash history to avoid false duplicate rejections across session boundaries.

---

## 4. Shared Memory (SHM) IPC Topology

### A. Single-Producer Multi-Consumer (SPMC)
- The POSIX/Windows shared memory engine is designed as a **Single-Producer Multi-Consumer** ring buffer.
- A single MDRAP publisher process writes sequentially to the ring, while multiple co-located client algorithms read concurrently using lock-free seqlock semantics.
- **Limitation**: Multiple publisher processes cannot write directly into the same SHM ring without an external lock or sequencer.

### B. Reader Lap and Overrun Handling
- If a client consumer process stalls or executes heavy analytical processing, the publisher will eventually wrap around the circular buffer (default 16,384 slots).
- When a reader detects that the publisher head has lapped its read position, the reader safely drops missed packets, increments its overrun counter, and fast-forwards to the publisher's current tail.
- Low-latency consumers must ensure their per-tick processing budget remains below the ring buffer wrap time.

---

## 5. Architectural & Package Scope

### A. Non-Core Deprecations
- Legacy analytics and derivatives pricing components (`mdrap-options`, `mdrap.backtest`, legacy `QuarantineManager`) are explicitly deprecated.
- Core engine engineering is strictly focused on market data ingestion, validation, normalization, reconciliation, and WAL durability.

### B. Distributed Consensus
- MDRAP operates as a node-local sidecar. Cross-region high-availability, leader election, and distributed consensus (e.g. Raft) must be orchestrated by external cluster managers (Kubernetes, Consul, Nomad) or messaging backbones (Aeron, Kafka).

---

## 6. Summary

These boundaries represent intentional, disciplined architectural tradeoffs balancing ultra-low latency, bounded memory usage, and uncompromising data integrity. When deployed within these parameters, MDRAP v4.0.1 delivers robust, predictable institutional performance.
