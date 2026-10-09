# MDRAP Phase 0: Repository and Architecture Inventory

**Document ID**: `MDRAP-AUDIT-P0-INV-001`  
**Phase**: Phase 0 (Baseline Audit & Invariant Specification)  
**Date**: 2026-10-09  
**Git Commit**: `b891898`  
**Classification**: Evidence-Backed Inventory  

---

## 1. Executive Overview

This inventory documents all source components, native kernels, data paths, and integration layers across the Market Data Reliability & Acceleration Platform (MDRAP). Every component is classified by operational status (**Active**, **Deprecated**, **Experimental**, **Duplicated**, or **Unused**), public API compatibility, downstream consumers, and test coverage evidence.

---

## 2. Component Inventory

### 2.1 Native C Hot-Path Acceleration & FFI
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Fastpath C Kernel** | `src/mdrap/fastpath.c` | **Active** | `FastQualityEngine`, `mdrap-core.exe`, `_fastpath_c.c` | `tests/test_fastpath.py`, `tests/test_fastpath_throughput.py`, `tests/test_parity_differential.py` | 85.4 KB C source. Implements 7 SIMD quality checks, 64-bit sliding window dedup, Welford variance, and seqlock slots in 43.9 ns. |
| **Native Core Daemon** | `src/mdrap/mdrap_core.c` | **Active** | CLI standalone executable (`mdrap-core.exe`) | `tests/test_build_fastpath.py`, `benchmarks/bench_mdrap_core.py` | Standalone C executable running wire-to-SHM with zero Python interpreter frames. |
| **CPython C Extension** | `src/mdrap/_fastpath_c.c` | **Active** | `fastpath.py` (via native import) | `tests/test_fastpath.py` | CPython native binding module providing PyCapsule and direct C struct marshalling. |
| **Fastpath JIT/Build Script** | `src/mdrap/build_fastpath.py` / `build_fastpath.py` | **Active** | Build system, CI workflows | `tests/test_build_fastpath.py` | Discovers GCC, Clang, or MSVC and produces `_fastpath_native.dll`/`.so`, `mdrap-core.exe`, and `.pyd`. |
| **Fastpath Python Bridge** | `src/mdrap/fastpath.py` | **Active** | `pipeline.py`, `engine.py`, `quality.py` | `tests/test_fastpath.py`, `tests/test_parity_differential.py` | Dynamically loads native DLL via `ctypes` or C extension with transparent fallback to pure Python. |
| **Native Fuzzers** | `fuzz/fuzz_sbe.c`, `fuzz/fuzz_batch.c` | **Active** | LibFuzzer / Security CI | `fuzz/` test scripts | Validates SBE packet parser and batch allocator resilience against malformed inputs. |

---

### 2.2 Shared Memory (SHM) IPC & Zero-Copy Streaming
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **SHM Ring Buffer v3** | `src/mdrap/shm.py` | **Active** | `shm_drainer.py`, `client.py`, `mdrap-core.exe` | `tests/test_shm.py`, `tests/test_shm_decoupled.py`, `tests/test_shm_drainer.py` | Lock-free SPMC ring buffer. 128-byte cache-line aligned slots, 64-byte padded writer heartbeat line, 2-phase commit protocol. |
| **SHM Background Drainer** | `src/mdrap/shm_drainer.py` | **Active** | `service.py`, persistence layer | `tests/test_shm_drainer.py` | Consumes slots from SHM ring buffer and drains to IngestLog binary WAL and SQLite asynchronously. |

---

### 2.3 Data Models, Encodings & Symbology
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Canonical Models** | `src/mdrap/models.py` | **Active** | Core pipeline, storage, client | `tests/test_canonical_models.py` | Core dataclasses: `RawEvent`, `CanonicalEvent`, `QualityStatus`, `Reason`. Includes fixed-point decimal integer scaling. |
| **Symbology Directory** | `src/mdrap/symbology.py` | **Active** | `gateway.py`, `models.py` | `tests/test_symbology.py` | ISO 10383 MIC mappings, ISO 4217 currency mappings, ticker resolution. |
| **Simple Binary Encoding** | `src/mdrap/sbe.py` | **Active** | `mdrap-core.exe`, `shm.py`, `itch.py` | `tests/test_sbe.py` | CME MDP 3.0 / FIX standard SBE zero-copy framing (128-byte tick frames). |
| **Wire Protocol** | `src/mdrap/protocol.py` | **Active** | `gateway_tcp.py`, network services | `tests/test_protocol.py` | Binary packet framing, magic numbers, checksum headers. |
| **NASDAQ ITCH 5.0 Engine** | `src/mdrap/itch.py` | **Active** | Ingress, `mbo.py`, backtesting | `tests/test_itch.py` | Complete NASDAQ TotalView-ITCH 5.0 binary protocol decoder and packet generator. |
| **Market-By-Order (MBO)** | `src/mdrap/mbo.py` | **Active** | `itch.py`, L3 depth engine | `tests/test_mbo.py` | Level-3 order book lifecycle state machine (Add, Exec, Cancel, Replace). |
| **Multicast A/B Arbitrator** | `src/mdrap/multicast_arbitrator.py` | **Experimental** | Direct exchange UDP feeds | `tests/test_multicast_arbitrator.py` | Dual physical feed arbitration (Line A vs Line B) with watermark dedup and TCP replay gap recovery. |

---

### 2.4 Feed Gateway & Ingress Adapters
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Feed Gateway** | `src/mdrap/gateway.py` | **Active** | `pipeline.py`, `engine.py`, `live.py` | `tests/test_pipeline_integration.py` | Monotonic ID generation (`itertools.count(1)`), receipt timestamping, schema normalization. |
| **Feed Supervisor** | `src/mdrap/feed_handler.py` | **Active** | CLI live streaming, API services | `tests/test_feed_handler.py` | Multiplexes crypto WS, Polygon, Databento, and simulator into unified stream. |
| **Crypto WebSocket Feeds** | `src/mdrap/ws_feed.py` | **Active** | `feed_handler.py`, CLI | `tests/test_ws_feed.py` | Real-time multi-exchange WebSocket manager (Binance, Coinbase, Kraken, OKX, Bybit). Includes drop counters under backpressure. |
| **Polygon.io Feed** | `src/mdrap/polygon_feed.py` | **Active** | `feed_handler.py`, CLI | `tests/test_polygon_feed.py` | Polygon.io WebSocket client for US Equities and Forex. |
| **Databento DBN Streamer** | `src/mdrap/databento_feed.py` | **Active** | `feed_handler.py`, CLI | `tests/test_databento_feed.py` | Databento binary stream replayer and live socket decoder. |
| **Feed Simulator** | `src/mdrap/simulator.py` | **Active** | Benchmarks, test harnesses | `tests/test_simulator.py` (via conftest) | Deterministic synthetic generator with statistical fault injection (drops, spikes, crossed quotes). |
| **PCAP Replayer** | `src/mdrap/pcap.py` | **Active** | ITCH/Multicast tests | `tests/test_phase21_feed_recovery_and_pcap.py` | Reads raw network packet captures (`.pcap`) and replays binary payloads. |

---

### 2.5 Data Quality, Rules & Reconciliation
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Quality Engine** | `src/mdrap/quality.py` | **Active** | `pipeline.py`, `engine.py` | `tests/test_quality.py`, `tests/test_quality_hardening_matrix.py` | Pure Python stateful 7-rule engine maintaining bit-identical parity with C kernel. |
| **Rules DSL Definition** | `src/mdrap/rules.def` / `rules.py` | **Active** | `quality.py`, `fastpath.c` | `tests/test_rules_single_source.py` | DSL defining bitmasks, severity, and thresholds across Python and C. |
| **Reconciler & Reliability** | `src/mdrap/reconciliation.py` | **Active** | `pipeline.py`, `engine.py` | `tests/test_reconciliation_invariants.py` | Cross-feed price divergence detection and EWMA source reliability scoring. |
| **Quarantine Store** | `src/mdrap/quarantine.py` | **Active** | `storage.py`, audit inspection | `tests/test_quarantine_subsystem.py`, `tests/test_quarantine_merkle.py` | Manages invalid and suspicious events with raw payload preservation for forensics. |

---

### 2.6 Persistence, WAL & Storage
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **IngestLog WAL** | `src/mdrap/ingestlog.py` | **Active** | `engine.py`, `service.py`, `shm_drainer.py` | `tests/test_ingestlog.py` | Mandatory binary Write-Ahead Log. Fixed-segment rotation, CRC-32 header verification, directory fsync. |
| **Binary Journal (.dbn)** | `src/mdrap/journal.py` | **Active** | Replay, fast dump | `tests/test_journal.py` | Append-only 128-byte memory-mapped transaction log matching SHM slot layout. |
| **SQLite Storage Tier** | `src/mdrap/storage.py` | **Active** | `pipeline.py`, projection, audit queries | `tests/test_storage_backends_independent.py` | SQLite WAL mode, 256MB mmap, 64MB cache, batched commits (`executemany`). |
| **SQLite Projection Layer** | `src/mdrap/projection.py` | **Active** | `engine.py`, `service.py` | `tests/test_projection.py` | Asynchronous projection reader updating SQLite from IngestLog stream. |
| **Columnar Store (DuckDB)**| `src/mdrap/columnar.py` | **Active** | Historical analytics, Parquet export | `tests/test_columnar.py` | Vectorized DuckDB storage and Apache Parquet export with Snappy/Zstd. |
| **Multi-Timeframe BarDB** | `src/mdrap/bardb.py` | **Active** | Candle generation, backtesting | `tests/test_bardb.py` | Incremental OHLCV bar roll-up engine (1s to 1d) with volume-weighted average price (VWAP). |
| **Raw Archive** | `src/mdrap/archive.py` | **Deprecated** | Legacy pipeline | `tests/test_archive.py` | JSONL write-ahead raw archive. Superseded by `ingestlog.py` in v3.0.0. |

---

### 2.7 Failover, Watchdog & Recovery
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Source Watchdog** | `src/mdrap/watchdog.py` | **Active** | `pipeline.py`, `reconciliation.py` | `tests/test_watchdog.py` | Real-time silence detection, degradation alerts, and automated feed failover. |
| **Feed Recovery Engine** | `src/mdrap/recovery.py` | **Active** | `chaos.py`, network handlers | `tests/test_gate_g2_kill9.py`, `tests/test_failover_and_dr.py` | Institutional state machine: DISCONNECTED -> CONNECTING -> SNAPSHOT -> LIVE -> GAP_DETECTED -> RECOVERING. |
| **Chaos Drill Engine** | `src/mdrap/chaos.py` | **Active** | Disaster recovery test suites | `tests/test_chaos.py`, `tests/test_chaos_drills_v26.py` | Automated chaos injection: feed drops, packet bursts, storage locks, and latency spikes. |

---

### 2.8 Security, RBAC & Audit
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Security Manager** | `src/mdrap/security.py` | **Active** | `api.py`, `storage.py`, `gateway_tcp.py` | `tests/test_security.py`, `tests/test_entitlements.py`, `tests/test_audit_chain_tamper.py` | HMAC-SHA256 API token hashing, RBAC (VIEWER/OPERATOR/ADMIN), token-bucket rate limiter, and Merkle-chained tamper-evident audit log. |
| **Audit Log Formats** | `src/mdrap/audit_format.py` | **Active** | `security.py`, forensic verification | `tests/test_audit_hardening.py` | Binary V1 and V2 Merkle audit block serializers with SHA-256 state chaining. |

---

### 2.9 Auxiliary & Domain Specific Modules
| Component | File Location | Status | Primary Downstream Consumers | Test Coverage | Evidence & Architectural Notes |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Consolidated NBBO** | `src/mdrap/bbo.py` | **Active** | `tca.py`, strategy SDK | `tests/test_bbo.py` | Multi-feed top-of-book aggregation, locked/crossed market detection, quote TTL pruning. |
| **Consolidated Depth L2** | `src/mdrap/depth.py` | **Active** | Trading analytics, API | `tests/test_depth.py` | Aggregated multi-venue order book ladders, micro-price, and order flow imbalance (OFI). |
| **Order Flow Tracker** | `src/mdrap/flow_tracker.py` | **Active** | Microstructure analytics | `tests/test_flow_tracker.py` | Lee-Ready (1991) trade sign classification, Cumulative Volume Delta (CVD), whale block detection. |
| **Transaction Cost (TCA)**| `src/mdrap/tca.py` | **Experimental** | Compliance / Execution analytics | `tests/test_tca.py` | SEC 605/606 and MiFID II RTS 27/28 slippage and price improvement engine. |
| **Options Pricing** | `src/mdrap/options.py` | **Experimental** | Derivatives desk | `tests/test_options.py` | Black-Scholes-Merton and binomial options pricing with Greeks chain (Delta to Volga). |
| **Portfolio Risk Engine** | `src/mdrap/risk.py` | **Experimental** | Risk management | `tests/test_risk.py` | Historical, parametric, and Monte Carlo VaR / CVaR calculations. |
| **Vessel Intelligence** | `src/mdrap/vessel.py` | **Deprecated** | Commodity freight tracking | `tests/test_vessel.py` | AIS ship tracking. Marked deprecated in v3.0.0 (non-core to market data engine). |
| **News Sentiment** | `src/mdrap/news.py` | **Experimental** | Alpha research | `tests/test_news.py` | News headline sentiment scoring and event tagging. |
| **FX Matrix** | `src/mdrap/fx.py` | **Deprecated** | Currency conversion | `tests/test_fx.py` | Currency pair tri-arbitrage matrix. Marked deprecated in v3.0.0. |

---

## 3. Real Execution Path Analysis

### Path 1: Hot-Path Native Wire-to-SHM Execution
```
[Market Wire / UDP / SBE Packet]
       │
       ▼
[mdrap-core.exe (Standalone C Daemon)]
       │
       ├─► SBE Zero-Copy Frame Unpack
       ├─► 64-bit Sliding Bitmap In-Memory Dedup
       ├─► 7 SIMD Quality Checks (Welford 6-σ, Stale, Crossed)
       ├─► Monotonic Sequence Number Assignment
       │
       ▼
[Shared Memory Ring Buffer (128-byte Aligned Slots)]
       │
       ├─► Co-located Algorithmic Trading Engines (Sub-300 ns IPC)
       └─► Asynchronous SHM Drainer Thread -> IngestLog WAL / SQLite
```
* **Execution Latency**: 43.9 ns (compute) + ~150–300 ns (SHM commit).
* **Guarantees**: Zero Python GIL overhead, lock-free seqlock publishing, zero dynamic heap allocations.

### Path 2: In-Process Python Ingestion Pipeline
```
[Raw Vendor Frame (JSON / Databento / WebSocket)]
       │
       ▼
[gateway.ingest()] -> Assign monotonic evt-N ID & gateway wall-clock timestamp
       │
       ▼
[gateway.normalize()] -> Map to CanonicalEvent schema (checks numeric fields)
       │
       ▼
[FastQualityEngine.evaluate()] -> Fastpath C FFI validation (or pure Python fallback)
       │
       ▼
[Reconciler.reconcile()] -> Cross-feed arbitration & EWMA reliability score
       │
       ▼
[Persistence Dispatch]
       ├─► Sync/Async IngestLog WAL (Durability Boundary)
       └─► SQLite Store (WAL mode, batched commits via executemany)
```
* **Execution Latency**: ~36 µs processing latency, ~780 µs end-to-end latency.
* **Guarantees**: 100% bit-identical status and reasons to Native C kernel; bad events quarantined.

---

## 4. Duplicate & Deprecated Paths Identified

1. **Dual WAL Implementations**:
   * `src/mdrap/archive.py` (`RawArchive`): Legacy JSONL line-based writer.
   * `src/mdrap/ingestlog.py` (`IngestLog`): High-throughput binary segment WAL with CRC-32 headers.
   * *Finding*: `RawArchive` is officially deprecated in v3.0.0; all durable write boundaries must use `IngestLog`.
2. **Dual Flat vs Package Shims**:
   * Files in `src/*.py` are backward-compatibility forwarders delegating directly into `src/mdrap/*.py`.
   * *Status*: Active for backward compatibility; clean and functional.
3. **Out-of-Scope Domain Modules**:
   * `vessel.py` (AIS vessel tracking), `news.py` (news sentiment), and `fx.py` (FX triangulation) represent scope sprawl from legacy prototypes and are marked deprecated/non-core.
