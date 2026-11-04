# MDRAP v5 Architecture Decision Records (ADRs)

## Index of Records
- [ADR-001: Native C Run-to-Completion Hotpath with Out-of-Path Python Control Plane](#adr-001)
- [ADR-002: Ingestion-Centric Binary WAL (IngestLog) Decoupled from Relational Projections](#adr-002)
- [ADR-003: Single-Threaded Exchange Channel Sequencing Prior to Any Demuxing](#adr-003)
- [ADR-004: Lock-Free SPSC Queues with C11 Acquire/Release Memory Ordering](#adr-004)
- [ADR-005: Zero-Allocation Steady-State Memory Design with 128-Byte NativeSlot Pools](#adr-005)
- [ADR-006: Asynchronous Micro-Batched SQLite Projections](#adr-006)
- [ADR-007: Seqlock-Protected Multi-Reader Shared Memory IPC Ring Buffer](#adr-007)
- [ADR-008: Heterogeneous CPU Core Affinity (P-Cores for Hotpath, E-Cores for Projection)](#adr-008)
- [ADR-009: Hardware-Accelerated CRC32C Frame Integrity and Clean Torn-Write Truncation](#adr-009)
- [ADR-010: Continuous Tri-Engine Parity Verification Against Pure-Python Golden Oracle](#adr-010)

---

### ADR-001: Native C Run-to-Completion Hotpath with Out-of-Path Python Control Plane {#adr-001}
- **Status**: Accepted
- **Context**: In MDRAP v4.0.1, the ingestion loop was driven by Python. Calling C via ctypes/cffi on a per-event basis incurs a $418\ \text{ns}$ boundary overhead per tick, while the C validation kernel takes only $38.8\ \text{ns}$ (`benchmark_micro_ffi.log`). Furthermore, Python object allocations account for $26.7\%$ of in-memory time (`benchmark_stage_breakdown.log`).
- **Decision**: Invert control: move the entire ingress, decode, sequencing, deduplication, quality validation, WAL append, and SHM publishing loop into a single native C11 runtime (`src/v5/`). Python is relegated strictly out-of-path for administrative commands, CLI orchestration, configuration, and telemetry scraping.
- **Consequences**:
  - *Positive*: Throughput capability increases from $\sim 46,000\ \text{EPS}$ to $> 10,000,000\ \text{EPS}$; in-memory p50 latency drops from $18.1\ \mu\text{s}$ to sub-microsecond.
  - *Negative*: Development requires strict C memory safety practices (checked bounds, static asserts, sanitizers).
- **Alternatives Considered**: Keeping Python as the main loop with batched FFI calls. Rejected because ingress socket polling and event framing in Python still incurred high interpreter overhead.

---

### ADR-002: Ingestion-Centric Binary WAL (IngestLog) Decoupled from Relational Projections {#adr-002}
- **Status**: Accepted
- **Context**: In v4.0.1, SQLite write operations were placed synchronously on the ingestion path. Even with SQLite WAL mode, single-writer B-tree locking throttled ingestion to $2,767.8\ \text{EPS}$ with p99 latency of $3.52\ \text{ms}$ (`benchmark_full_pipeline.log`).
- **Decision**: Introduce a dedicated, sequential, append-only binary log file (`IngestLog`). An event is considered durably staged and acknowledged to clients as soon as its frame is appended to `IngestLog`. SQLite database projection is decoupled into an asynchronous background worker.
- **Consequences**:
  - *Positive*: Ingestion-to-ACK latency drops from milliseconds to $0.8\text{--}1.8\ \mu\text{s}$; throughput is decoupled from SQLite disk sync performance.
  - *Negative*: System maintains two storage formats: binary WAL for recovery/durability and SQLite for ad-hoc querying.
- **Alternatives Considered**: In-memory only SQLite with periodic snapshotting. Rejected because it violated the zero-data-loss durability contract during process crashes.

---

### ADR-003: Single-Threaded Exchange Channel Sequencing Prior to Any Demuxing {#adr-003}
- **Status**: Accepted
- **Context**: Exchange protocols (e.g., NASDAQ ITCH via MoldUDP64, CME MDP 3.0) define monotonic sequence numbers per multicast channel, not per individual stock symbol. Partitioning incoming network packets by symbol before sequence verification conceals sequence gaps across symbols and violates feed-level integrity.
- **Decision**: Enforce that each exchange multicast channel is assigned to a dedicated, single-threaded run-to-completion ingress worker. Sequence numbers and gap detection are evaluated strictly at the channel boundary before any routing or downstream dispatching occurs.
- **Consequences**:
  - *Positive*: 100% deterministic sequence gap detection and duplicate rejection; eliminates inter-thread race conditions on sequence tracking.
  - *Negative*: Scaling across multiple feeds requires multiple channel worker threads.

---

### ADR-004: Lock-Free SPSC Queues with C11 Acquire/Release Memory Ordering {#adr-004}
- **Status**: Accepted
- **Context**: Thread-to-thread communication via mutexes or condition variables causes thread preemption, priority inversion, and latency spikes exceeding $10\ \mu\text{s}$.
- **Decision**: Use single-producer single-consumer (SPSC) circular ring buffers with cache-line-padded head and tail pointers (64-byte alignment) and C11 atomics (`atomic_load_explicit` with `memory_order_acquire`, `atomic_store_explicit` with `memory_order_release`).
- **Consequences**:
  - *Positive*: Enqueue/dequeue latency is bounded to $15\text{--}30\ \text{ns}$; zero lock contention; zero kernel context switches.
  - *Negative*: Ring capacity is fixed at compile/init time (power-of-two sizing required).

---

### ADR-005: Zero-Allocation Steady-State Memory Design with 128-Byte NativeSlot Pools {#adr-005}
- **Status**: Accepted
- **Context**: In profiling, Python dataclass allocation accounted for $26.7\%$ of processing time. Dynamic memory allocation causes heap fragmentation and CPU cache thrashing.
- **Decision**: Pre-allocate all operational buffers at startup. Represent events in contiguous arrays of 128-byte `NativeSlot` structures, occupying exactly two 64-byte L1 cache lines. Ban all `malloc`, `free`, and PyObject instantiation during steady-state processing.
- **Consequences**:
  - *Positive*: Zero heap churn; L1/L2 cache locality is preserved; deterministic execution time.
  - *Negative*: Fixed maximum in-flight capacity; requires explicit ring sizing.

---

### ADR-006: Asynchronous Micro-Batched SQLite Projections {#adr-006}
- **Status**: Accepted
- **Context**: SQLite exhibits superior bulk insertion performance when operations are grouped into transactions via `executemany` (over 100,000 rows/sec), but degrades dramatically on single-row inserts.
- **Decision**: The asynchronous projection worker dequeues events from the SPSC ring in micro-batches of up to 1,000 slots (or a 10 ms timeout) and executes a single batched transaction with `PRAGMA synchronous = NORMAL`.
- **Consequences**:
  - *Positive*: SQLite insertion throughput exceeds 150,000 EPS without affecting the hotpath.
  - *Negative*: Relational database view lags the live market feed by up to 10 ms.

---

### ADR-007: Seqlock-Protected Multi-Reader Shared Memory IPC Ring Buffer {#adr-007}
- **Status**: Accepted
- **Context**: Downstream algorithmic trading strategies require instantaneous, zero-copy access to canonical events. Sockets and IPC pipes introduce kernel copies and context switches.
- **Decision**: Implement a POSIX / Windows Shared Memory circular ring buffer governed by a Sequence Lock (Seqlock). The engine writes continuously; readers read lock-free and detect concurrent modifications via atomic sequence counter validation.
- **Consequences**:
  - *Positive*: Publish-to-read IPC latency is $65\text{--}110\ \text{ns}$; supports multiple concurrent readers without writer contention.
  - *Negative*: Readers that fall behind the ring's circular capacity are lapped and must resynchronize.

---

### ADR-008: Heterogeneous CPU Core Affinity (P-Cores for Hotpath, E-Cores for Projection) {#adr-008}
- **Status**: Accepted
- **Context**: The baseline processor is an Intel Core i7-1255U with 2 P-Cores and 8 E-Cores. Un-pinned threads migrating between core types suffer $30\text{--}50\%$ throughput drops and severe tail latency spikes.
- **Decision**: Explicitly pin the hotpath ingress and validation worker to P-Core 0 (Logical Thread 0), the WAL flush worker to P-Core 0 (Thread 1), and background SQLite projection and telemetry workers to E-Cores (Cores 4 and 5) via OS affinity APIs.
- **Consequences**:
  - *Positive*: Eliminates scheduler jitter; maximizes CPU cache residency on high-performance cores.
  - *Negative*: Requires platform-specific thread pinning code for Windows and Linux.

---

### ADR-009: Hardware-Accelerated CRC32C Frame Integrity and Clean Torn-Write Truncation {#adr-009}
- **Status**: Accepted
- **Context**: Power failure or hard process crash during disk logging can leave a partially written frame at the tail of the WAL file.
- **Decision**: Guard every `IngestLogFrame` with a hardware-accelerated CRC32C checksum (using SSE4.2 / ARM CRC instructions). The crash recovery scanner scans sequentially; the first frame with an invalid magic or CRC32C mismatch triggers immediate, clean file truncation to the last valid boundary.
- **Consequences**:
  - *Positive*: Mathematically prevents corrupted partial records from being ingested or projected; recovery completes in $< 250\ \text{ms}$.
  - *Negative*: Adds 4 bytes of overhead per frame and $\sim 15\ \text{ns}$ calculation time.

---

### ADR-010: Continuous Tri-Engine Parity Verification Against Pure-Python Golden Oracle {#adr-010}
- **Status**: Accepted
- **Context**: Introducing a C native core risks subtle behavioural discrepancies with the reference Python logic.
- **Decision**: Retain the complete pure-Python engine and pipeline as the authoritative `GoldenOracle`. Run continuous automated differential fuzzing (`tests/golden_oracle.py`) asserting 100% identical outputs across all validation states, price band thresholds, crossed book conditions, and sequence gap detections.
- **Consequences**:
  - *Positive*: Guarantees zero regression and ensures pure-Python fallback remains 100% production-viable on platforms without C compiler toolchains.
  - *Negative*: Any future rule changes must be implemented in both C and Python.
