# MDRAP v5.2 — Repository Forensics & Provenance Audit

## 1. System Provenance & State Anchor

```
+---------------------------------------------------------------------------------------------------------+
|                                    REPOSITORY FORENSIC SNAPSHOT                                         |
+---------------------------------------------------------------------------------------------------------+
| Attribute                      | Verified Value / State Anchor                                          |
+--------------------------------+------------------------------------------------------------------------+
| Target Development Branch      | v5.2-development                                                      |
| Predecessor Release Branch     | v5.1.1-development                                                    |
| Certified HEAD Commit SHA      | ead6d69310bd42d5db1b037a7689872f19fbdf4b                               |
| Ancestry Baseline Commit SHA   | db37a3e4af410daee5157ff00487a2a89dd7717b (v5.1 Certified Base)         |
| Working Tree Status            | Clean (0 unstaged modifications, 0 untracked files)                   |
| Operating System Runtime       | Microsoft Windows 11 Pro AMD64 (10.0.26100)                             |
| CPU Architecture               | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz (4C/8T)                 |
| Python Runtime                 | CPython 3.13.1 (MSC v.1942 64 bit)                                     |
| C Compiler / Toolchain         | Microsoft Visual C++ (MSVC) 19.42 / Build Tools 2022                   |
| Native Fastpath Library        | src/mdrap/_fastpath_native.dll / _fastpath_c.cp313-win_amd64.pyd       |
| Total Source Modules           | 68 core modules in src/mdrap/ + 2 native C kernels                     |
| Subpackages                    | packages/mdrap-strategies, packages/mdrap-options                      |
| Test Coverage Base             | 1,726 tests passing, 0 failures, 63 deselected                         |
+---------------------------------------------------------------------------------------------------------+
```

---

## 2. Source Inventory & Component Classification

```
+---------------------------------------------------------------------------------------------------------+
|                                 MDRAP COMPONENT FORENSIC CLASSIFICATION                                 |
+---------------------------------+-------------------------+------------------+--------------------------+
| Subsystem Component             | Source File Location    | Language / Type  | Architectural Tier       |
+---------------------------------+-------------------------+------------------+--------------------------+
| Pipeline Orchestrator           | src/mdrap/engine.py     | Python 3.13      | Hot-Path Core Engine     |
| Native Hot-Path Kernel          | src/fastpath.c          | C11 / MSVC DLL   | Native Fastpath Tier     |
| Fastpath Python Bridge          | src/mdrap/fastpath.py   | Python / ctypes  | Fastpath Fallback Tier   |
| Seqlock Shared Memory (v3)      | src/mdrap/shm.py        | Python / Win32   | IPC Distribution Tier    |
| Binary Write-Ahead Log (WAL)    | src/mdrap/ingestlog.py  | Python (struct)  | Persistence Tier         |
| Async SQLite Projections        | src/mdrap/storage.py    | Python / SQLite  | Asynchronous Query Tier  |
| Crash & Snapshot Recovery       | src/mdrap/recovery.py   | Python stdlib    | Durability Tier          |
| SBE / NASDAQ-ITCH Parser        | src/mdrap/sbe.py,itch.py| Python / struct  | Binary Decoding Tier     |
| Multi-Venue WebSocket Feeds     | src/mdrap/ws_feed.py    | Python / asyncio | Ingress Network Tier     |
| Live Exchange REST Connector    | src/mdrap/live.py       | Python / urllib  | Ingress Network Tier     |
| Realistic Matching Engine       | packages/mdrap-strategies| Python stdlib   | Simulation / EMS Tier    |
| Institutional Risk Engine (14G) | packages/mdrap-strategies| Python stdlib   | Pre-Trade Risk Tier      |
| Microstructure Research Library | packages/mdrap-strategies| Python / math    | Quantitative Alpha Tier  |
| Execution Tear Sheet Analytics  | packages/mdrap-strategies| Python stdlib   | TCA & Analytics Tier     |
| Backends (Hist, Paper, Testnet) | packages/mdrap-strategies| Python stdlib   | Execution Interface Tier |
+---------------------------------+-------------------------+------------------+--------------------------+
```

---

## 3. End-to-End Event Lifecycle & Execution Flow

```
[Exchange WS/REST] ──(Wire JSON/Bytes)──► [Ingress & Normalize] ──(RawEvent)──► [Gateway Normalizer]
                                                                                        │
                                                                                 (CanonicalEvent)
                                                                                        │
                                                                                        ▼
[Crash Recovery] ◄──(Binary Replay)─── [Write-Ahead Log] ◄──(Framed WAL)────── [FastQualityEngine]
        │                                                                               │
(Replayed State)                                                                 (QualityStatus)
        │                                                                               │
        ▼                                                                               ▼
[SQLite Storage] ◄──(Batch Executemany)─ [Async Projections] ◄────────────── [Multi-Venue Sequencer]
                                                                                        │
                                                                                        ▼
[Strategy Engine] ◄──(Zero-Copy Read)─── [SHM v3 Seqlock] ◄──(Atomic Write)─── [SPSC Ring Queue]
        │
   (Order Intent)
        │
        ▼
[Pre-Trade Risk Engine] ──(14 Gates Validation)──► [PASSED] ──► [Realistic Matching Engine / EMS]
        │                                                                        │
    [REJECTED]                                                                   ▼
        │                                                               [Order Book Priority Queues]
        ▼                                                                        │
 [Risk Tombstone]                                                          (Fill Frictions)
                                                                                 │
                                                                                 ▼
                                                                        [TCA Tear Sheet Analytics]
```

### Forensic Lifecycle Matrix:
1. **Ingress & Normalization**:
   - Input: WebSocket JSON string or binary SBE buffer.
   - Ownership: Network I/O thread (`WebSocketFeedManager`).
   - Memory: Dynamic allocation in socket buffer, deserialized into `RawEvent`.
   - Latency: ~25.0 µs per JSON frame.
2. **Quality Evaluation**:
   - Input: `CanonicalEvent` with fixed `slots=True`.
   - Ownership: Pipeline engine worker thread.
   - Implementation: Native C fastpath kernel (`_fastpath_native.dll`) with 100% semantic fallback to pure Python `FastQualityEngine`.
   - Priority Invariant: `INVALID > SUSPICIOUS > VALID`. Never downgraded.
   - Latency: ~7.0 µs (Native C), ~23.8 µs (Pure Python).
3. **Sequencing & Isolation**:
   - Checks per-venue monotonic sequence counters (`venue_seq`).
   - Gaps and duplicates detected without cross-venue state leakage.
4. **IPC Distribution**:
   - Input: Validated canonical tick.
   - Ownership: IPC publisher thread.
   - Implementation: Seqlock protected 64-byte aligned ring buffer (`SHMWriter` / `SHMReader`).
   - Latency: ~8.4 µs.
5. **Pre-Trade Risk Validation**:
   - Enforces 14 deterministic gates: Fat-finger collars, price collars (50 bps), drawdown kill-switch (5%), stale quotes, idempotency deduplication.
   - Latency: <1.2 µs.
6. **Execution Simulation & EMS**:
   - Orders sorted in strict price-time priority.
   - Requires resting volume `queue_ahead_qty` to clear before execution.
   - Scheduled on priority heap for decision-to-arrival latency and cancel/fill race conditions.
7. **Durability & Persistence**:
   - Non-durable mode: In-memory segment buffering (~68,500 EPS).
   - Direct durable mode: `fsync` flush on every append (~890 EPS on physical SSD).
   - SQLite projection: Background batched `executemany` with WAL mode.
