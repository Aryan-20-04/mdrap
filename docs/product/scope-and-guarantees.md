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

### 4.3 Acknowledgement Boundary Semantics (Spec §5 & Strategy §6.1)

MDRAP explicitly decomposes event lifecycle persistence into 6 distinct stages (`AckStatus`):

| Boundary | `AckStatus` | Technical State & Guarantee |
|---|---|---|
| **`received`** | `RECEIVED (1)` | Byte frame arrived from network socket or IPC descriptor into process memory. |
| **`validated`** | `VALIDATED (2)` | Frame passed structural schema validation, required fields, and bounds checks. |
| **`accepted`** | `ACCEPTED (2)` | Event accepted into engine deduplication and fold state machine. |
| **`buffered`** | `BUFFERED_APP (3)` | Encoded frame staged in user-space IngestLog memory buffer. |
| **`journaled`** | `JOURNALED (4)` | Record flushed to OS kernel page cache via `write()` (`WRITTEN_OS`). |
| **`durable`** | `DURABLE (5)` | Physical `fsync` completed; data persisted to non-volatile disk (`DURABLY_COMMITTED`). |
| **`recovered`** | `RECOVERED (6)` | Event re-parsed and state reconstructed during WAL replay. |
| **`published`** | `PUBLISHED (7)` | Canonical event committed to seqlock SHM ring or dispatched to TCP subscribers. |

> [!IMPORTANT]
> **Durability Guarantee:** An event is NEVER acknowledged as `durable` until physical storage synchronization (`fsync`) has succeeded.

### 4.4 Explicit Operational Modes & Overload Contracts (Strategy §2 & §5.4)

MDRAP supports 3 distinct operational modes (`OperationalMode`), eliminating contradictions between bounded queues and zero-drop claims:

1. **`LOSS_DETECTING` (Live Stream Mode)**:
   - *Intended Use:* Live visual dashboards, exploratory research, and resource-constrained environments ("potato PCs").
   - *Overload Policy:* Bounded queue capacity. If downstream storage or processing stalls, the oldest frames are evicted to preserve real-time low latency. **Every dropped frame increments `_drop_count`, logs a warning, and flags the stream as `DEGRADED`. Dropping is never silent.**
2. **`DURABLE_CAPTURE` (Forensic Capture Mode)**:
   - *Intended Use:* Quantitative historical capture, compliance recording, and production execution replay.
   - *Overload Policy:* An event is acknowledged only after `DURABLE` (`fsync`). If disk I/O stalls, backpressure is propagated to the ingress socket. If backpressure fails or queue overflows, capture halts immediately with an explicit fatal error rather than silently discarding unpersisted data.
3. **`LOSSLESS_TO_SOURCE` (Deterministic Recovery Mode)**:
   - *Intended Use:* Institutional feeds with upstream retransmission protocols (e.g. Nasdaq ITCH SoupBinTCP, CME TCP historical replay).
   - *Overload Policy:* Bounded local memory; sequence gaps trigger retransmission requests or snapshot resynchronization. Losslessness is guaranteed only to the extent supported by upstream exchange recovery.

### 4.5 Terminology Disambiguation & Buzzword Elimination

To ensure technical precision and avoid misleading claims, MDRAP strictly defines its core architectural terms:

| Buzzword | Marketing Misconception | MDRAP Technical Reality & Exact Definition |
|---|---|---|
| **"Durable"** | "Saved in memory / fast write" | Physical persistence to non-volatile media confirmed by synchronous `fsync()`. OS page cache writes are classified as `journaled`, not `durable`. |
| **"Lossless"** | "Queues never drop" | Strictly bounded to `LOSSLESS_TO_SOURCE` protocols with sequence retransmission. Bounded memory queues under sustained overload will either apply backpressure or drop with explicit telemetry. |
| **"Real-Time"** | "Instantaneous / zero delay" | Strictly bounded by empirical latency distributions ($p50, p90, p99, p99.9$) measured under specified workloads with hardware disclosures. |
| **"Zero-Copy"** | "No data movement anywhere" | Elimination of user-space Python object and heap allocations between memoryview slicing and mapped shared memory (`/dev/shm`). Kernel socket and DMA copies remain unless hardware-qualified kernel-bypass is configured. |
| **"Lock-Free"** | "Thread-safe for all topologies" | Single-Producer Single-Consumer (SPSC) seqlock ring buffer without kernel mutexes. Requires explicit CPU cache-line alignment (64 bytes) and memory fences; not interchangeable with multi-producer queues. |

### 4.6 Timestamp Discipline & Clock Domains

Every canonical event explicitly separates four distinct clock domains:
1. `exchange_timestamp`: Timestamp declared by the originating exchange venue.
2. `receive_timestamp`: Userspace monotonic clock timestamp when packet arrived at socket.
3. `processing_timestamp`: Monotonic timestamp when normalization and quality validation completed.
4. `clock_source`: Explicit enum declaring timestamp source (`"EXCHANGE"`, `"GATEWAY_RECV"`, `"HARDWARE_NIC"`).

---

## 5. Performance Claims Policy

1. **No Universal Numbers:** All published throughput and latency figures represent specific observed runs on specified hardware, operating systems, and workloads.
2. **Delivery Rate Bound:** Multi-consumer delivery rate is mathematically bounded at $\le 100.0\%$. Background simulator traffic must never contaminate subscriber receipt counts.
3. **Tail Latency Reporting:** System benchmarks must report $p50$, $p90$, $p99$, $p99.9$, and maximum latencies generated from saved raw sample arrays.
4. **Environment Manifest Disclosure:** Every benchmark proof must accompany a machine-readable JSON manifest recording CPU, RAM, OS, compiler flags, and input dataset SHA-256 hash.

