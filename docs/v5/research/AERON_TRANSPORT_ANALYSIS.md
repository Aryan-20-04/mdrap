# MDRAP v5.0 — Aeron Transport & Log-Buffer Architectural Analysis

**Document ID:** `docs/v5/research/AERON_TRANSPORT_ANALYSIS.md`  
**Classification:** Research Analysis & Transport Evaluation  
**Auditor / Architect:** Principal Low-Latency Systems Architect & Storage/Transport Specialist  
**Evidence Standard:** Primary Source Documentation & Empirical Architectural Comparison  

---

## 1. Primary Reference & Industry Context

- **Source Reference:** Real Logic / Aeron (Martin Thompson, Todd Montgomery)
  - URL: https://aeron.io/docs/aeron/overview/
  - Publications & Subscriptions: https://aeron.io/docs/aeron/publications-subscriptions/
- **Primary Problem Solved:** Provides ultra-low-latency, high-throughput message transport across IPC (inter-process communication) and UDP network boundaries, implementing zero-copy log buffers, lock-free concurrency, and deterministic flow control.

---

## 2. Core Aeron Architectural Mechanisms

### 2.1 The Log Buffer & Three-Term Partitioning
- `[EXTERNAL-RESEARCH]` Aeron coordinates message streams using memory-mapped **Log Buffers**.
- Each log buffer channel is divided into **three term partitions** (Term 0, Term 1, Term 2).
- At any point in time:
  - One term is **active** (receiving new publications).
  - One term is **clean** (zeroed and ready for the next term rotation).
  - One term is **dirty / draining** (being consumed by subscribers or archived to disk).
- **Benefit**: Eliminates dynamic memory allocation and ensures that writing to the log buffer is purely sequential. When the active term fills up, the producer seamlessly rotates to the clean term while background worker threads clean the prior dirty term.

### 2.2 Buffer Claim Pattern (Zero-Copy Publication)
- `[EXTERNAL-RESEARCH]` Traditional IPC messaging copies data twice:
  $$\text{Application Buffer} \xrightarrow{\text{Copy 1}} \text{IPC Shared Buffer} \xrightarrow{\text{Copy 2}} \text{Consumer Buffer}$$
- Aeron introduces `tryClaim()`:
  1. The publisher requests a claim for $N$ bytes.
  2. The sequencer atomically advances the term offset.
  3. The publisher receives a direct pointer into the memory-mapped log buffer.
  4. The publisher writes the message payload **directly into shared memory in-place**.
  5. The publisher commits the sequence header.
- **Copy Reduction**: Reduces publication overhead to zero intermediate memory copies.

### 2.3 Media Driver Separation
- `[EXTERNAL-RESEARCH]` Aeron separates the client API from the I/O engine via an out-of-process or embedded **Media Driver**:
  - The Media Driver manages UDP sockets, retransmissions, flow control, and conductor threads.
  - The application thread only interacts with shared memory log buffers via atomic sequence updates.
- **Operational Cost**: Running an external Media Driver daemon introduces deployment complexity, inter-process signal coordination, and additional monitoring overhead.

### 2.4 Backpressure & Flow Control
- `[EXTERNAL-RESEARCH]` Aeron implements strict backpressure based on subscriber window feedback:
  - If a slow consumer lags behind and the active term partitions fill up, `tryClaim()` returns `ADMIN_ACTION` or `BACK_PRESSURED`.
  - The publisher is explicitly prevented from overwriting unconsumed messages.
- **Trade-off in Market Data**:
  - In transactional trading systems, backpressuring the exchange feed is impossible (the exchange socket drops packets if the receiver halts).
  - For downstream subscribers, backpressure must be configurable: either drop with observable overrun metrics or pause non-critical consumers.

---

## 3. Comparative Evaluation: Aeron vs. MDRAP Custom SHM

| Architectural Dimension | Aeron IPC | MDRAP v4.0.1 Custom SHM (`shm.py`) | MDRAP v5.0 Recommended Strategy |
| :--- | :--- | :--- | :--- |
| **External Dependencies** | Requires Aeron C/Java library + Media Driver daemon | **Zero external dependencies** (Pure Python + stdlib / native C) | **Retain Zero External Dependencies** |
| **Deployment Complexity** | High (daemon orchestration, shm directory config) | Low (single-binary / library invocation) | **Low (Embedded native C kernel)** |
| **Slot / Buffer Design** | Variable-length log terms (64KB–1GB terms) | Fixed 128-byte cache-aligned ring slots | **Fixed 128-byte cache-aligned slots** |
| **Write Protocol** | `tryClaim()` $\rightarrow$ write $\rightarrow$ `commit()` | Two-phase seqlock write (`commit_seq`) | **Adopt Aeron `tryClaim()` In-Place Write** |
| **Consumer Overrun** | Strict backpressure or term unmap | Lap detection $\rightarrow$ fast-forward + telemetry | **Lap detection + observable counters** |
| **Language Support** | C, C++, Java, .NET, Go | Python + C native extension | **CPython C-API + Native C CLI** |

---

## 4. Architectural Verdict for MDRAP v5.0

1. **Do NOT adopt Aeron as an external binary dependency `[INFERENCE]`**:
   - Importing Aeron would introduce heavy C++ runtime dependencies, external daemon processes, and operational friction for co-located MDRAP sidecars without providing measurable latency improvements over custom shared memory on single-host IPC.
2. **Adopt Aeron's Core Design Patterns in MDRAP Native C `[SOURCE-VERIFIED]`**:
   - **Zero-Copy In-Place Buffer Claiming**: Allow the feed handler to decode SBE / ITCH messages directly into the pre-allocated SHM slot pointer rather than decoding into an intermediate struct.
   - **Seqlock Two-Phase Publication**: Retain MDRAP's 128-byte cacheline-aligned slot format (`HEADER_SIZE = 128`, `SLOT_SIZE = 128`), ensuring single-cycle commit without external daemons.

---

## 5. Evidence Classification Summary

- `[EXTERNAL-RESEARCH]` Aeron architecture documentation and primary publications.
- `[SOURCE-VERIFIED]` `src/mdrap/shm.py`, `src/mdrap/mdrap_core.c` (custom SHM ring buffer layout and seqlock commit).
- `[INFERENCE]` Aeron media driver deployment trade-offs.
