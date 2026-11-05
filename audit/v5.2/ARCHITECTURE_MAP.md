# MDRAP v5.2 — Institutional Architecture Map

## 1. System Decomposition & Module Boundaries

MDRAP is partitioned into six decoupled functional tiers:

```
+───────────────────────────────────────────────────────────────────────────────────+
| 1. INGRESS & NORMALIZATION TIER                                                   |
|    - ws_feed.py: Persistent WebSocket feeds (Binance, Coinbase, Kraken, OKX, Bybit)|
|    - live.py: Synchronous REST connectors & symbol symbology mapping              |
|    - sbe.py / itch.py: NASDAQ TotalView-ITCH 5.0 & CME SBE binary wire parsers    |
|    - gateway.py: RawEvent -> CanonicalEvent normalization schema engine           |
+───────────────────────────────────────────────────────────────────────────────────+
                                          │
                                          ▼
+───────────────────────────────────────────────────────────────────────────────────+
| 2. QUALITY VALIDATION & SEQUENCING TIER                                           |
|    - fastpath.c / fastpath.py: Native C / Pure Python differential quality engine |
|    - engine.py: Core state machine, window state, fault classification            |
|    - symbology.py: Multi-venue ticker normalization and asset class routing       |
|    - consensus.py: BBO reconciliation across asynchronous feeds                   |
+───────────────────────────────────────────────────────────────────────────────────+
                                          │
                                          ▼
+───────────────────────────────────────────────────────────────────────────────────+
| 3. IPC & DISTRIBUTION TIER                                                        |
|    - shm.py: Seqlock-protected Single-Producer Multi-Consumer (SPMC) ring buffer  |
|    - async_fanout.py / supervisor.py: Multi-channel isolated core supervisors     |
|    - af_xdp.py: Kernel-bypass networking interfaces (Linux / DPDK blueprints)     |
+───────────────────────────────────────────────────────────────────────────────────+
                                          │
                                          ▼
+───────────────────────────────────────────────────────────────────────────────────+
| 4. PERSISTENCE & FAULT TOLERANCE TIER                                             |
|    - ingestlog.py: Append-only binary Write-Ahead Log (WAL) with CRC32C framing   |
|    - storage.py: Asynchronous SQLite projection engine (WAL mode, executemany)    |
|    - recovery.py: Deterministic snapshot and WAL replay recovery engine           |
+───────────────────────────────────────────────────────────────────────────────────+
                                          │
                                          ▼
+───────────────────────────────────────────────────────────────────────────────────+
| 5. PRE-TRADE RISK & EXECUTION SIMULATOR TIER                                      |
|    - risk_engine.py: 14 pre-trade institutional risk gates & kill switches        |
|    - matching_engine.py: Discrete-event realistic price-time priority simulator   |
|    - backends/: Historical replay (A), Live paper (B), Exchange testnet (C)       |
+───────────────────────────────────────────────────────────────────────────────────+
                                          │
                                          ▼
+───────────────────────────────────────────────────────────────────────────────────+
| 6. QUANTITATIVE RESEARCH & TCA ANALYTICS TIER                                     |
|    - microstructure.py: Avellaneda-Stoikov, Micro-Price, Cont OFI, Lee-Ready      |
|    - tear_sheet.py: Transaction Cost Analysis (TCA), markouts, slippage, Sharpe   |
|    - api.py: FastAPI / Uvicorn WebSocket & REST streaming endpoints               |
+───────────────────────────────────────────────────────────────────────────────────+
```

---

## 2. In-Depth Subsystem Specifications

### 2.1 Ingress & Normalization Tier
- **Wire Deserialization**:
  - WebSockets parse incoming JSON frames into `RawEvent` objects.
  - Binary feeds (CME SBE and NASDAQ ITCH 5.0) use zero-copy `struct.unpack_from` over pre-allocated bytearrays.
- **Normalization Contracts**:
  - Output is an immutable `CanonicalEvent` with fixed slots (`__slots__ = (...)`).
  - Strict timestamp preservation: `exchange_timestamp` (source exchange clock), `receive_timestamp` (local gateway clock), and `processing_timestamp` (post-validation clock).

### 2.2 Quality Engine Tier
- **Dual-Kernel Architecture**:
  - `_fastpath_native.dll`: Native C hot-path implementation compiled with MSVC `/O2 /Oi /Ot`.
  - `FastQualityEngine`: 100% semantic pure-Python fallback.
  - Parity is guarded by regression tests: both kernels produce bit-for-bit identical `QualityStatus` verdicts.
- **Priority Rule**:
  `QualityStatus.INVALID (3) > QualityStatus.SUSPICIOUS (2) > QualityStatus.VALID (1)`.
  A status can never be downgraded once set.

### 2.3 Shared Memory (SHM v3) IPC Architecture
- **Seqlock Protocol**:
  - Sequence counters start at 0.
  - Writer increments sequence to an odd number before updating the payload slot, issues memory barrier, copies payload, issues memory barrier, and increments sequence to an even number.
  - Readers read sequence before and after payload copy. If sequence is odd or changed between reads, the reader re-reads.
- **Platform Portability**:
  - Windows: Backed by `CreateFileMapping` / `MapViewOfFile` via Win32 API.
  - POSIX / Linux: Backed by `shm_open` / `mmap`.

### 2.4 Persistence Tier (Binary WAL)
- **Record Framing**:
  - Fixed 32-byte header: `Magic (4B) | Version (2B) | Flags (2B) | Seq (8B) | Timestamp (8B) | PayloadLen (4B) | CRC32C (4B)`.
  - Payloads serialized in compact binary format.
- **Durability Modes**:
  - `policy="never"`: Buffered in userspace memory, flushed during rotation or shutdown (~68.5k EPS).
  - `policy="always"`: Hardware `fsync` after every record append (~890 EPS on physical NVMe).

### 2.5 Execution & Pre-Trade Risk Tier
- **14 Pre-Trade Risk Gates**:
  - All orders pass `validate_order()` before entering the execution queue.
  - Rejection latency is under 1.2 microseconds.
- **Microstructure Simulator**:
  - Orders rest behind prevailing book depth (`queue_ahead_qty`).
  - Arrival and cancellation delays are scheduled on a discrete-event priority heap (`heapq`).
  - Fills record adverse selection markouts at 10ms, 100ms, and 1s.
