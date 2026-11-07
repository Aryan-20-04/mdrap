# MDRAP — Architecture Review & Systems Design

**System**: Market Data Reliability & Acceleration Platform (MDRAP)  
**Document**: Architectural Evaluation, Component Consolidation & Data Flow  
**Target Environment**: Production Institutional Trading Infrastructure  

---

## 1. Architectural Overview & Design Philosophy

MDRAP serves as an institutional market data validation, normalization, deduplication, reconciliation, and audit sidecar. It sits upstream of algorithmic trading engines, execution management systems (EMS), quantitative analytics, and historical tick databases.

### Core Architecture Tenets
1. **Authoritative Immutable Journal**: The `IngestLog` write-ahead log (WAL) is the single immutable source of truth. All downstream projections (SQLite, DuckDB, Parquet) are rebuildable materialized views.
2. **Deterministic State Transitions**: `state, decision = engine.step(state, raw_event, clock)` is a pure, reproducible state fold. State can be snapshotted, restored, or deterministically replayed from any historical checkpoint.
3. **Dual-Execution Engine Parity**: Pure Python stdlib reference implementation guarantees 100% semantic output parity with the Native C hot-path accelerator (`_fastpath_c.c`, `fastpath.c`).
4. **Decoupled Low-Latency IPC**: Output streaming distribution is decoupled via lock-free Shared Memory ring buffers (SHM v3) with seqlock two-phase commit verification.

---

## 2. End-to-End Data Flow

```mermaid
flowchart TD
    subgraph INGRESS["1. Market Data Ingress"]
        F1["Direct ITCH / SBE Wire Bytes"]
        F2["WebSocket Streams (Binance/Coinbase/OKX)"]
        F3["Institutional Feeds (Polygon/Databento)"]
    end

    subgraph GATEWAY["2. Gateway & Normalization"]
        GW["Gateway.normalize()"]
        TSN["Timestamp Auto-Scaling (ns/us/ms -> s)"]
        SCH["Input Sanitization & Schema Bounds Check"]
    end

    subgraph WAL["3. Authoritative Persistence"]
        IL["IngestLog WAL (Append-Only)"]
        FS["Group Commit & Fsync Engine"]
    end

    subgraph ENGINE["4. Deterministic Core Engine"]
        STEP["Engine.step() Pure State Fold"]
        FAST["Native C Fastpath Accelerator (501k EPS)"]
        PYFB["Pure Python Fallback Engine (185k EPS)"]
        DEDUP["64-Bit Sliding Bitmap Dedup"]
        CORR["Welford Price Corridor & Outlier Filter"]
        CROSS["Crossed Quote & Temporal Sorter"]
    end

    subgraph DISTRIBUTION["5. Distribution & Materialization"]
        SHM["SHM v3 Lock-Free IPC Ring Buffer (274k EPS)"]
        WS["FastAPI WebSocket Distribution (Loopback)"]
        SQL["Materialized SQLite Projection (Cached Checkpoints)"]
        QUAR["Cryptographic Quarantine Dead-Letter Store"]
        AUDIT["Tamper-Evident Merkle Audit Trail"]
    end

    F1 --> GW
    F2 --> GW
    F3 --> GW

    GW --> TSN --> SCH --> IL
    IL --> FS --> STEP

    STEP --> FAST
    STEP --> PYFB
    FAST --> DEDUP --> CORR --> CROSS
    PYFB --> DEDUP --> CORR --> CROSS

    CROSS -->|VALID / SUSPICIOUS| SHM
    CROSS -->|VALID / SUSPICIOUS| WS
    CROSS -->|VALID / SUSPICIOUS| SQL
    CROSS -->|INVALID Poison| QUAR

    STEP --> AUDIT
```

---

## 3. Component Evaluation & Consolidation

### 3.1 The Three Quality Implementations Reconciled

The audit identified three distinct quality evaluation modules across the repository:
1. `QualityEngine` in `src/mdrap/quality.py`: Object-oriented pipeline evaluator designed for modular pipeline assemblies.
2. `FastQualityEngine` in `src/mdrap/fastpath.py`: JIT-loading wrapper delegating to compiled native C `fastpath.c`, with pure Python fallback.
3. `Engine._evaluate_*` in `src/mdrap/engine.py`: Pure function state fold used inside the deterministic IngestLog execution loop.

#### Reconciliation & Invariant Enforcement
- **Single Source of Truth**: All reason flags and bitmasks derive strictly from `rules.def`.
- **Reason Parity**: Prior to audit remediation, `Engine._stage_quote_and_staleness` omitted `Reason.TS_IMPLAUSIBLE`. Future timestamp skew checking (`exch_ts > recv_ts + max_future_skew_s`) has been integrated into `engine.py`, establishing 100% reason code parity across all three implementations.
- **Semantic Test Parity**: Differential fuzz testing (`tests/test_parity_differential.py`) and event type parity tests (`tests/test_phase5_event_type_parity.py`) verify identical classification across Native C and Pure Python.

### 3.2 Downstream Materialized Projections

- **IngestLog vs SQLite**: In earlier iterations of the platform, SQLite writes on `submit()` introduced a 36% latency bottleneck due to synchronous B-Tree queries and fsync locking.
- **Architectural Fix**:
  - The IngestLog WAL is the sole synchronous write on the critical path.
  - Projections utilize `ProjectionTracker` with support for `async_projections=True`.
  - In-memory checkpoint caching eliminates redundant `SELECT last_offset` disk queries on each transaction.

### 3.3 Components Consolidated & Safely Deprecated

1. **Non-Core Analytical Subsystems**: Subsystems such as `mdrap.backtest`, `mdrap.options`, and `mdrap.quarantine.QuarantineManager` were previously entangled with core market data ingestion. These have been migrated to standalone packages (`packages/mdrap-options`, `packages/mdrap-analytics`) or consolidated into direct SQLite quarantine storage.
2. **Direct Wire Ingestion Fastpath**: Direct wire decoding (ADR-007) bypasses intermediate JSON dictionary allocation, directly parsing ITCH and SBE binary buffers into `CanonicalEvent` structures at **>13.5M EPS**.

---

## 4. Architectural Trade-offs

| Architectural Decision | Chosen Implementation | Alternative Considered | Engineering Trade-off Justification |
|---|---|---|---|
| **Memory Buffer Model** | Lock-free Shared Memory (SHM v3) with 64-byte seqlock | ZeroMQ / TCP sockets | Shared memory achieves **<2 µs latency** and zero kernel context switches; TCP sockets add 15–40 µs networking stack overhead. |
| **State Persistence** | Append-only binary WAL + Periodic snapshots | Direct SQLite transactions on ingestion | WAL enables sequential I/O write performance (>45k EPS); relational database engines suffer write amplification and lock contention. |
| **C Extension Strategy** | Hybrid ctypes + CPython C-API (`_fastpath_c.c`) | Pure C rewrite / Cython | Preserves 100% pure Python fallback capability on constrained or non-standard environments while offering C execution speed on AMD64/ARM64. |
| **Error Handling** | Quarantine `INVALID` events; flag `SUSPICIOUS` | Silent dropping or strict exception halting | Dropping bad data blinds risk systems; throwing exceptions crashes ingestion during market volatile periods. Quarantining preserves full forensic audit lineage. |
