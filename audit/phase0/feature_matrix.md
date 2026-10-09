# MDRAP Phase 0 — Feature and Capability Matrix

**Document Identifier**: `MDRAP-AUDIT-P0-MATRIX-001`  
**Phase**: Phase 0 (Baseline Audit Only)  
**Classification Categories**:
- `Implemented & Tested`: Present in source code and verified by passing regression tests.
- `Implemented Insufficiently Tested`: Present in source code, but lacks edge-case, crash-resilience, or adversarial test suites.
- `Experimental`: Present in repository but marked unstable, incomplete, or feature-flagged.
- `Deprecated`: Superseded in v3.0 architecture but retained for backward compatibility.
- `Not Found`: Mentioned in architecture specifications, comments, or documentation but absent from the codebase.

---

## 1. Feature & Capability Matrix

| Subsystem / Feature | Category | Implementation Status | Source Files | Test Files | Known Limitations / Caveats |
|---|---|---|---|---|---|
| **Native C Core Hot Path** | Core / Kernel | `Implemented & Tested` | [`fastpath.c`](src/fastpath.c), [`mdrap_core.c`](src/mdrap_core.c) | `test_fastpath.py`, `test_golden_parity.py` | 16-char symbol truncation limit; MSVC build lacks AVX2 compile flags by default. |
| **Pure Python Fallback Engine** | Core / Fallback | `Implemented & Tested` | [`quality.py`](src/quality.py), [`gateway.py`](src/gateway.py) | `test_parity_differential.py` | Runs at ~300k eps vs 5.01M eps for native C core; high memory overhead per object. |
| **Shared Memory (SHM v3) Ring Buffer** | IPC | `Implemented & Tested` | [`shm.py`](src/shm.py), [`fastpath.c`](src/fastpath.c) | `test_shm.py`, `test_shm_decoupled.py` | Windows Named SHM and POSIX `/dev/shm` supported. 16-char symbol slot limit. |
| **Two-Phase Seqlock Protocol** | Concurrency | `Implemented & Tested` | [`fastpath.c`](src/fastpath.c), [`shm.py`](src/shm.py) | `test_shm_fuzz.py`, `test_shm_watermark.py` | Relies on x86 TSO memory ordering; ARM weak-memory model requires explicit DMB fences. |
| **Welford Rolling Volatility Corridor** | Data Quality | `Implemented & Tested` | [`fastpath.c`](src/fastpath.c), [`quality.py`](src/quality.py) | `test_quality.py`, `test_fastpath_quantitative.py` | Sensitive to initial warm-up sample count (min 20 ticks required before sigma check). |
| **Sliding Window Sequence Deduplication** | Data Quality | `Implemented & Tested` | [`fastpath.c`](src/fastpath.c), [`quality.py`](src/quality.py) | `test_rules_single_source.py` | 64-bit sliding bitmap; out-of-order jumps > 64 sequence positions fall through to LRU. |
| **Out-of-Order Reordering Buffer** | Data Quality | `Implemented Insufficiently Tested` | [`quality.py`](src/quality.py) | `test_quality.py` | Returns `None` while buffering; silently leaks ticks if caller omits `drain_expired()`. |
| **Cross-Feed Reconciliation Tracker** | Reconciler | `Implemented & Tested` | [`reconciliation.py`](src/reconciliation.py) | `test_reconciliation_invariants.py`, `test_decision_identity.py` | In-memory reliability scoring using EWMA; does not survive process restarts. |
| **IngestLog Binary WAL** | Durability | `Implemented & Tested` | [`journal.py`](src/journal.py) | `test_journal.py`, `test_journal_durability.py` | Framed binary WAL with CRC32 checksums; fsync policy configurable (`always` vs `never`). |
| **SQLite Batched Storage Engine** | Persistence | `Implemented & Tested` | [`storage.py`](src/storage.py) | `test_storage.py` | WAL mode with 2,000-row batching. Asynchronous writer thread can lose in-flight queue on crash. |
| **Parquet Columnar Archive Exporter** | Persistence | `Implemented & Tested` | [`columnar.py`](src/columnar.py), [`archive.py`](src/archive.py) | `test_columnar.py`, `test_export.py` | Requires `pyarrow` (pure-Python fallback available with JSONL). |
| **SBE Binary Market Data Decoding** | Protocols | `Implemented & Tested` | [`sbe.py`](src/sbe.py), [`fastpath.c`](src/fastpath.c) | `test_sbe.py` | High throughput zero-copy parser for fixed-layout SBE tick frames. |
| **NASDAQ ITCH 5.0 Feed Adapter** | Protocols | `Implemented Insufficiently Tested` | [`itch.py`](src/itch.py) | `test_itch.py` | Mock-only unit tests; real binary capture test skipped in standard CI due to missing bin sample. |
| **Multi-Exchange WebSocket Manager** | Feed Ingress | `Implemented & Tested` | [`ws_feed.py`](src/ws_feed.py) | `test_ws_feed.py` | Adapters for Binance, Kraken, Coinbase, OKX, Bybit; includes drop counter telemetry. |
| **Databento Live Ingestion** | Feed Ingress | `Implemented Insufficiently Tested` | [`databento_feed.py`](src/databento_feed.py) | `test_databento_feed.py` | Live tests skipped in CI due to missing external API key. |
| **Polygon.io Live Ingestion** | Feed Ingress | `Implemented Insufficiently Tested` | [`polygon_feed.py`](src/polygon_feed.py) | `test_polygon_feed.py` | Live tests skipped in CI due to missing external API key. |
| **Consolidated NBBO Engine** | Analytics | `Implemented & Tested` | [`bbo.py`](src/bbo.py) | `test_bbo.py` | Computes synthetic national best bid/offer across multi-venue feeds with TTL expiration. |
| **Consolidated L2 Depth Engine** | Analytics | `Implemented & Tested` | [`depth.py`](src/depth.py) | `test_depth.py` | Aggregates full book depth ladders with locked/crossed detection. |
| **Transaction Cost Analysis (TCA)** | Analytics | `Implemented & Tested` | [`tca.py`](src/tca.py) | `test_tca.py` | Computes VWAP slippage, market impact, and spread capture metrics. |
| **Options Greeks & Implied Volatility** | Analytics | `Implemented & Tested` | [`options.py`](src/options.py) | `test_options.py` | Black-Scholes analytical formulas; floating-point precision only (no fixed-point representation). |
| **Historical Bar Database (BarDB)** | Analytics | `Implemented & Tested` | [`bardb.py`](src/bardb.py) | `test_bardb.py` | OHLCV aggregation across 1s, 1m, 5m, 1h buckets. |
| **FastAPI REST & WebSocket Server** | API Service | `Implemented & Tested` | [`api.py`](src/api.py) | `test_api.py`, `test_api_server.py` | Full REST CRUD and WebSocket real-time pub/sub distribution. |
| **Raw TCP Gateway Server** | API Service | `Implemented Insufficiently Tested` | [`gateway_tcp.py`](src/gateway_tcp.py) | `test_transport_hardening.py` | Sequential broadcast can stall async loop; mTLS tests not run in standard CI. |
| **Role-Based Access Control (RBAC)** | Security | `Implemented & Tested` | [`security.py`](src/security.py) | `test_security.py`, `test_entitlements.py` | Enforces VIEWER, OPERATOR, ADMIN roles; contains token hash authentication fallback defect. |
| **Cryptographic Merkle Audit Log** | Security | `Implemented & Tested` | [`security.py`](src/security.py), [`audit_format.py`](src/audit_format.py) | `test_security.py` | Tamper-evident hash chain across all state transitions. |
| **Source Heartbeat Watchdog** | Operability | `Implemented & Tested` | [`watchdog.py`](src/watchdog.py) | `test_watchdog.py` | Detects stale/frozen vendor feeds and triggers failover notifications. |
| **Prometheus Metrics Exporter** | Operability | `Implemented & Tested` | [`prometheus.py`](src/prometheus.py) | `test_prometheus.py` | Exposes standard OpenMetrics format for scrape targets. |
| **Interactive Terminal TUI Dashboard** | Operability | `Implemented & Tested` | [`terminal_display.py`](src/terminal_display.py), [`term.py`](src/term.py) | `test_terminal_display.py` | Full Rich-based live operations console. |
| **Kernel-Bypass Multicast Ingress (Solarflare EF_VI / DPDK)** | Ingress | `Not Found` | N/A | N/A | Claimed in high-level architectural references; not implemented in codebase. |
| **Hardware PTP Nanosecond NIC Stamping** | Ingress | `Not Found` | N/A | N/A | Hardware timestamping registers (SO_TIMESTAMPING) absent; relies on OS clock. |
| **Distributed Raft / Paxos Replication** | Clustering | `Not Found` | N/A | N/A | System is currently single-node sidecar with local storage and SHM. |
| **Legacy Single-Process `Pipeline` Class** | Core | `Deprecated` | [`pipeline.py`](src/mdrap/pipeline.py) | Multiple legacy tests | Emits `DeprecationWarning` in v3.0.0; superseded by `IngestLog` + `Engine` + `SQLiteProjection`. |
| **Legacy `config.yaml` Loader** | Configuration | `Deprecated` | [`config.py`](src/config.py) | `test_config.py` | Emits `DeprecationWarning`; superseded by `mdrap.toml` via `config_loader.py`. |

---

## 2. Capability Summary

1. **Production-Ready Capabilities**:
   - High-throughput Native C engine core (~5M eps).
   - Windows Named / POSIX Shared Memory IPC with sub-microsecond latency.
   - Comprehensive rule validation and strict quarantine routing.
   - FastAPI REST / WebSocket streaming with Merkle audit trails.
2. **Key Architectural Limitations**:
   - No kernel-bypass network drivers (relies on standard OS socket stacks).
   - Single-node architecture without distributed leader election.
   - 16-character identifier limits in fixed C shared-memory slot structures.
   - Deprecated monolithic `Pipeline` classes still used across benchmarks and legacy scripts.
