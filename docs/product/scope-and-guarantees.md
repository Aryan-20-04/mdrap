# MDRAP Product Scope & Guarantees Specification

**Document Version:** 1.0.0  
**Effective Date:** 2026-10-15  
**Target Release:** MDRAP v3.1.1  
**Applicability:** Core Single-Node Distribution (`mdrap-core`)

---

## 1. Product Identity & Purpose

MDRAP (**Market Data Reliability & Acceleration Platform**) is a specialized, embedded software engine designed to normalize, validate, and durably capture raw financial market data streams on a **single computing node**.

It operates at the boundary between raw market feed adapters (WebSockets, ITCH binary, FIX/FAST, REST snapshots) and downstream consumer applications (algorithmic trading strategies, analytical pipelines, pricing engines, tick databases).

---

## 2. Supported Deployment Topology

MDRAP supports **single-node execution only**:
- **In-Process Embedded Library (`mdrap.Engine`):** Direct Python API for applications embedding validation, quality filtering, and WAL durability within a single operating system process.
- **Single-Node Sidecar Daemon (`mdrap-daemon` / `mdrap service`):** Dedicated local daemon process that ingests feeds, commits raw data to the local Write-Ahead Log (WAL), and fans out canonical ticks to local consumers via:
  1. **POSIX / Windows Shared Memory Ring Buffer (`mdrap.shm`):** Sub-microsecond local inter-process communication (IPC) for co-located processes on the same physical host.
  2. **Localhost TCP Streaming:** Asynchronous socket distribution for local subscribers over loopback (`127.0.0.1`).

```
┌─────────────────────────────────────────────────────────────────┐
│                    SINGLE HOST / COMPUTE NODE                   │
│                                                                 │
│   ┌───────────────┐        ┌────────────────────────────────┐   │
│   │ Raw Feeds     │───────>│ MDRAP Ingest & Quality Engine  │   │
│   │ (WS/ITCH/TCP) │        └──────────────┬─────────────────┘   │
│   └───────────────┘                       │                     │
│                                           │ synchronous fsync   │
│                                           ▼                     │
│                            ┌────────────────────────────────┐   │
│                            │ Local Write-Ahead Log (WAL)    │   │
│                            │ (Segmented IEEE CRC-32 on disk)│   │
│                            └──────────────┬─────────────────┘   │
│                                           │                     │
│                        ┌──────────────────┴──────────────────┐  │
│                        │                                     │  │
│                        ▼                                     ▼  │
│         ┌─────────────────────────────┐       ┌─────────────┴┐  │
│         │ Shared Memory Ring Buffer   │       │ Local TCP    │  │
│         │ (SPSC Seqlock on /dev/shm)  │       │ Fan-Out      │  │
│         └──────────────┬──────────────┘       └──────┬───────┘  │
│                        │                             │          │
│                        ▼                             ▼          │
│         ┌─────────────────────────────┐       ┌──────────────┐  │
│         │ Local Co-located Consumer   │       │ Local Socket │  │
│         │ (Trading Strategy / Python) │       │ Subscriber   │  │
│         └─────────────────────────────┘       └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Explicit Non-Goals & Out-of-Scope Capabilities

MDRAP does **NOT** provide, and must not be used as:
1. **Distributed Consensus or Cluster High Availability:** MDRAP contains no distributed Raft/Paxos quorum protocol, no multi-host split-brain fencing, and no multi-datacenter replication. The `ConsensusCoordinator` class is strictly an in-memory simulation for local testing.
2. **Exchange Order Routing / Execution Management System (EMS/OMS):** MDRAP processes market data; it does not route orders, manage positions, or interface with execution gateways.
3. **Clearing, Settlement, or General Ledger:** MDRAP is not a financial ledger, double-entry book, or transactional payment system.
4. **Regulatory Reporting Compliance Certification:** MDRAP produces audit logs with cryptographic hash chains, but possession of MDRAP does not constitute MiFID II, CAT, or SEC regulatory certification.
5. **Universal Latency Guarantees:** Low latency is a function of hardware, kernel configuration, memory pinning, and operating system scheduling. MDRAP does not guarantee sub-microsecond latency across arbitrary platforms.

---

## 4. Architectural Boundaries & Guarantees

### 4.1 Data Quality & Validation Invariants
- **Strict Status Priority:** Every event is evaluated to a `QualityStatus`:
  $$\text{INVALID (2)} > \text{SUSPICIOUS (1)} > \text{VALID (0)}$$
  An event's status **can never be downgraded** (e.g. an `INVALID` event is never demoted to `SUSPICIOUS` or `VALID`).
- **Never Silently Discard Bad Data:** Malformed or invalid events are quarantined with an immutable reason code and exposed via metrics counters.
- **Strict Typing:** Python `bool` values (which subclass `int`) are explicitly rejected for numeric price, quantity, timestamp, and sequence fields.

### 4.2 Deduplication & Sequence Tracking Boundaries
- **Sequenced Feeds:** Tracked via monotonic integer sequence numbers per `(source, instrument)` stream.
  - Exact match: Flagged as `DUPLICATE` (`INVALID`).
  - Sequence regression: Flagged as `OUT_OF_ORDER` (`SUSPICIOUS`).
  - Sequence jump: Flagged as `SEQUENCE_GAP` (`SUSPICIOUS`).
- **Sequence Gap Detection vs. Recovery:** MDRAP detects and flags sequence gaps. MDRAP **does not** recover missing events over the wire unless an external upstream protocol (such as ITCH soup-bintcp replay) is connected.
- **Unsequenced Deduplication Window:** Feeds without sequence numbers rely on an in-memory content hash ring buffer bounded to 64 slots per instrument. Duplicates arriving outside this time/count window cannot be detected without external persistent index lookups.

### 4.3 Durability & WAL Contract
- **Durability Boundary (`IngestLog`):** Every acknowledged event is written to a segmented on-disk Write-Ahead Log.
- **Fsync Policies:**
  - `always`: Synchronous `os.fsync()` before returning acknowledgement (zero data loss on OS crash or power failure).
  - `grouped_by_size` / `grouped_by_time`: Buffered kernel flushing (potential uncommitted record loss on kernel panic up to buffer window).
- **Checksum Verification:** Frames are protected by IEEE 802.3 CRC-32 (standard `zlib.crc32`). Torn tails at EOF are detected and truncated during recovery.

### 4.4 Shared Memory (SHM) Lifecycle
- **Transport Contract:** Single-Producer Single-Consumer (SPSC) ring buffer with 128-byte slots aligned to cache lines.
- **Epoch Invalidation:** When a publisher restarts, readers must detect the new epoch, invalidate cursors, and reattach. Unlinked memory segments must not be silently consumed.

---

## 5. Performance Claims Policy

1. **No Universal Numbers:** All published throughput and latency figures represent specific observed runs on specified hardware, operating systems, and workloads.
2. **Delivery Rate Bound:** Multi-consumer delivery rate is mathematically bounded at $\le 100.0\%$. Background simulator traffic must never contaminate subscriber receipt counts.
3. **Tail Latency Reporting:** System benchmarks must report $p50$, $p95$, $p99$, and maximum latencies generated from saved raw sample arrays.
