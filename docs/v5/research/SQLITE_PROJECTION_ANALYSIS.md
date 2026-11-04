# MDRAP v5.0 — SQLite WAL & Analytical Projection Architectural Analysis

**Document ID:** `docs/v5/research/SQLITE_PROJECTION_ANALYSIS.md`  
**Classification:** Research Analysis & Storage Engine Evaluation  
**Auditor / Architect:** Principal Low-Latency Systems Architect & Storage/Durability Specialist  
**Evidence Standard:** Primary SQLite Documentation & Empirical Benchmark Analysis  

---

## 1. Primary Reference & Industry Context

- **Source Reference:** SQLite Consortium (D. Richard Hipp et al.)
  - URL: https://www.sqlite.org/wal.html
  - Locking and Concurrency: https://www.sqlite.org/lockingv3.html
- **Primary Role in MDRAP:** Serves as the queryable relational store for canonical market events, quarantine records, and historical analytical projections. **It is NOT the authoritative crash-recovery event log**; that role belongs exclusively to MDRAP's sequential binary Write-Ahead Log (`IngestLog`).

---

## 2. Core SQLite WAL Concurrency & Locking Mechanics

### 2.1 The Single-Writer Constraint
- `[EXTERNAL-RESEARCH]` Although SQLite WAL (Write-Ahead Logging) mode allows multiple readers to operate concurrently with a writer, **SQLite strictly enforces a single-writer constraint per database file**:
  - Only one connection can hold a write transaction (`BEGIN IMMEDIATE` or `BEGIN EXCLUSIVE`) at any time.
  - If multiple worker threads or processes attempt concurrent writes to the same SQLite database file, all but one will receive `SQLITE_BUSY` (`sqlite3.OperationalError: database is locked`) once the `busy_timeout` expires.
- **MDRAP Evaluation `[SOURCE-VERIFIED]`**:
  - Attempting to have multiple ingestion partition threads write concurrently to a single SQLite database causes immediate lock contention and massive tail latency spikes.
  - **Rule**: A single SQLite database file must have **exactly one dedicated writer connection**.

### 2.2 Reader/Writer Concurrency & Checkpoint Starvation
- `[EXTERNAL-RESEARCH]` In WAL mode:
  - Writes append new pages to the `.db-wal` file.
  - Readers read unmodified pages from `.db` and updated pages from `.db-wal` via the memory-mapped WAL index (`.db-shm`).
  - To prevent `.db-wal` from growing indefinitely, pages must be transferred back to `.db` via **checkpointing** (`PRAGMA wal_checkpoint`).
- **Checkpoint Starvation**:
  - A checkpoint cannot transfer pages past the read-mark of the **oldest active reader transaction**.
  - If analytical queries or background dashboard workers hold long-lived transactions, the WAL file cannot be checkpointed. It grows from megabytes to gigabytes, causing reader cache misses and degrading query latency.

### 2.3 PRAGMA Tuning for High-Throughput Ingestion
`[EXTERNAL-RESEARCH]` Optimal SQLite configuration for market data projections:
- `PRAGMA journal_mode = WAL;`: Enables write-ahead logging.
- `PRAGMA synchronous = NORMAL;`: In WAL mode, `NORMAL` guarantees database integrity across application and OS crashes, requiring `fsync` only during WAL checkpoint operations rather than on every transaction commit.
- `PRAGMA temp_store = MEMORY;`: Keeps temporary tables and indices in RAM.
- `PRAGMA cache_size = -64000;`: Allocates 64MB of page cache in RAM.
- `PRAGMA busy_timeout = 5000;`: Bounded wait on locks before raising error.

---

## 3. Empirical Bottleneck: Ingestion Thread Decoupling

From empirical MDRAP benchmarks:
- **In-Memory Engine Throughput `[BENCHMARK-MEASURED]`**: **46,000–77,000 EPS** ($12\text{--}18\ \mu\text{s}$ p50).
- **Synchronous SQLite Projection Pipeline `[BENCHMARK-MEASURED]`**: **2,760–4,600 EPS** ($150\text{--}190\ \mu\text{s}$ p50, $3.5\text{ ms}$ p99).
- **Diagnosis `[SOURCE-VERIFIED]`**:
  - The SQLite disk projection pipeline is **16x slower** than in-memory event processing.
  - In MDRAP v4.0.1, when `Engine.submit()` is called with an attached projection, the projection dispatch and transaction serialization occur on the caller's critical path unless explicitly queued via `ProjectionTracker`.
  - Transaction batching is mandatory: committing 1 event per transaction yields $< 300\text{ EPS}$; committing in micro-batches of 500–2,000 events achieves $> 4,500\text{ EPS}$.

---

## 4. Architectural Strategy for MDRAP v5.0

```
[Feed Ingress / C Hot Path] ──► [Binary WAL Append] ──► [Durable ACK to Client]
                                        │
                               (Async Ring Queue)
                                        │
                                        ▼
                         [Dedicated Projection Worker]
                                        │
                    (Batch 500-2000 events / 10ms deadline)
                                        │
                                        ▼
                         [Single-Writer SQLite DB (WAL)]
```

1. **Complete Asynchronous Decoupling `[SOURCE-VERIFIED]`**:
   - The ingestion critical path appends to the sequential binary WAL, records the offset, and returns the durability decision immediately.
   - Projections are updated asynchronously by a dedicated projection worker thread consuming from an in-memory queue.
2. **Authoritative Lineage & Replay Invariant `[SOURCE-VERIFIED]`**:
   - Every projection maintains an independent durable checkpoint: `last_offset`.
   - If the projection worker falls behind or crashes, it automatically replays from `last_offset + 1` from the authoritative binary WAL upon startup.
3. **Partitioned Projection Sharding `[INFERENCE]`**:
   - For multi-instrument feeds exceeding 10,000 EPS, MDRAP v5 will support **partitioned projection databases**:
     - `quotes_AAPL.db`, `quotes_MSFT.db`, etc. (or date/hour partitioned databases), each with its own single-writer thread, eliminating SQLite serialization limits.

---

## 5. Evidence Classification Summary

- `[EXTERNAL-RESEARCH]` SQLite official WAL and locking documentation.
- `[SOURCE-VERIFIED]` `src/mdrap/projection.py`, `src/mdrap/engine.py`.
- `[BENCHMARK-MEASURED]` `audit/v5/raw_evidence/benchmark_full_pipeline.log`.
