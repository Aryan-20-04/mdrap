# MDRAP Architecture Review — Product Reset

**Date**: 2026-11-11  
**Lead Architect**: Lead Engineering Architect  
**Review Type**: Strategic Decoupling & Modular Monolith Evaluation  

---

## 1. Architectural Boundaries Evaluation

The previous MDRAP architecture attempted an overly ambitious multi-tier topology (T0 pure Python, T1 C fastpath, T2 Verilog FPGA, T3 networked distributed cluster) while simultaneously packing retail charting, trading strategies, and order book matching into the same codebase.

### The Refocused Modular Monolith
The product reset enforces a clean, modular monolith:

```text
mdrap/
├── ws_feed.py      -> Ingestion & Connector Lifecycle (CORE)
├── gateway.py      -> Normalization & Symbology Mapping (CORE)
├── models.py       -> Domain Types & Enums (CORE)
├── provenance.py   -> Cryptographic Event Provenance & Lineage (CORE)
├── quality.py      -> Data Quality & Anomaly Detection (CORE)
├── watchdog.py     -> Feed Health Scoring & Routing Failover (CORE)
├── incidents.py    -> Anomaly Aggregation & Incident Correlation (CORE)
├── replay.py       -> Deterministic Historical & Incident Replay (CORE)
├── chaos.py        -> Connector Conformance & Resilience Certification (CORE)
├── storage.py      -> SQLite Relational Persistence (SUPPORTING)
├── wal.py          -> Append-Only Write-Ahead Log (SUPPORTING)
├── fastpath.py     -> Native C Quality Kernel & Vectorized FFI (SUPPORTING)
├── shm.py          -> Zero-Copy Lock-Free Shared Memory Ring Buffers (SUPPORTING)
└── security.py     -> HMAC Feed Auth, RBAC, Merkle Audit Chains (SUPPORTING)
```

---

## 2. Dependency Direction & Circularity Check

All core modules follow a strict single-direction dependency graph:
`models` $\to$ `provenance` $\to$ `gateway` $\to$ `quality` $\to$ `watchdog` $\to$ `incidents` $\to$ `replay` $\to$ `storage`.
Zero circular imports exist in the core processing flow.

---

## 3. Microservices vs. In-Process Sidecar

The review explicitly examined whether MDRAP should be decomposed into microservices.
**Conclusion**: **NO**.
Market-data validation operates at microsecond latencies ($1.5\text{ µs}$ hot path). Introducing gRPC, HTTP, or distributed microservices between ingestion, quality evaluation, and memory buffering would inflate tail latency by 2 to 3 orders of magnitude ($500\text{ µs} - 2\text{ ms}$). MDRAP's modular in-process architecture with optional shared-memory IPC (`shm.py`) is the optimal institutional deployment model.
