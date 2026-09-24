# MDRAP Project Baseline Report

**Execution Timestamp:** 2026-09-24 18:34:39 UTC+05:30  
**Baseline Git Reference:** Pre-Release Working Baseline  
**Environment Specification:** Isolated Local Host (Windows 11 x64, Python 3.13.1)

---

## 1. Executive Summary & Verification Gates

This document establishes the official frozen project baseline for the **Market Data Reliability & Acceleration Platform (MDRAP)** pursuant to Phase 1 of the Core Engineering Rule. All measurements, test outcomes, statements, and module catalogs represent the ground truth prior to architectural reorganization.

| Verification Gate | Result | Details |
| :--- | :--- | :--- |
| **Unit & Integration Suite** | **PASS** (100%) | **815 passed**, 60 deselected (`slow`/`network`), 0 failed |
| **Execution Duration** | 251.19s | Full end-to-end execution including async sinks and stress simulations |
| **Statement Coverage** | **76.25%** | 16,648 covered lines out of 21,833 total statements |
| **Native C Fastpath** | **PASS** | `_fastpath_native.dll` compiled via GCC 14.2.0; bitmask parity validated |
| **Rules Single-Source** | **PASS** | `tools/gen_reasons.py --check` validates 0 divergence between `rules.def`, C, & Python |
| **Diagnostic Doctor** | **PASS** | `cli.py doctor` verifies zero-dependency fallback integrity |

---

## 2. Host & Runtime Environment

```yaml
host_system:
  operating_system: "Windows-11-10.0.26200-SP0 (64-bit AMD64)"
  processor: "Intel64 Family 6 Model 154 Stepping 4, GenuineIntel"
  cpu_logical_cores: 12
  system_ram_gb: 15.72
compilers_and_runtimes:
  python: "3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 19:06:28) [MSC v.1942 64 bit]"
  c_compiler: "gcc.exe (Rev2, Built by MSYS2 project) 14.2.0"
  docker: "Docker version 29.2.1, build a5c7197"
core_dependencies:
  pytest: "8.3.4"
  pytest-asyncio: "0.24.0"
  pytest-timeout: "2.3.1"
  pytest-cov: "6.0.0"
  duckdb: "1.5.5"
  pyarrow: "23.0.1"
  fastapi: "0.115.0"
  uvicorn: "0.30.6"
  websockets: "15.0.1"
  rich: "15.0.0"
  openpyxl: "3.1.5"
  pyyaml: "6.0.3"
  zstandard: "0.25.0"
```

---

## 3. Comprehensive Repository Inventory

### 3.1 Directory Topology

- **`src/`**: 73 Python modules, 1 extension package (`adapters/`), C fastpath sources (`fastpath.c`, `mdrap_core.c`), single-source-of-truth definition (`rules.def`).
- **`tests/`**: 79 test modules spanning unit, integration, RBAC security, chaos drills, fault recovery, and WebSocket distribution.
- **`benchmarks/`**: 111 benchmark harnesses, historical baseline runs, differential JSON proofs, and micro-benchmarks (`bench_stress_and_resilience.py`, `micro_ffi.py`, `stage_breakdown.py`).
- **`scripts/`**: Operational utilities for database backups (`backup.py`, `backup.sh`), disaster recovery restore (`restore.py`, `restore.sh`), and environment checks.
- **`docs/`**: Architecture diagrams, ADRs (`docs/decisions/`), user manuals, extension guides (`docs/extending/`), and security specifications.
- **Docker & Deployment**: `Dockerfile` (multi-stage non-root container), `docker-compose.yml`, `Caddyfile` (reverse proxy with TLS & rate limiting).
- **Examples**: `examples/` directory containing institutional strategy walkthroughs and custom adapter boilerplates.
- **Configs**: `config.yaml` (full system settings), `mdrap.toml` (package/project manifest), `pytest.ini` (test marks and warning filters), `.env.example`.
- **Native Acceleration**: `src/fastpath.c`, `src/mdrap_core.c`, `build_fastpath.py`, and compiled artifacts (`_fastpath_native.dll`, `mdrap-core.exe`).

---

### 3.2 Complete Module Matrix (`src/`)

Every module in `src/` declares its stability tier (`__stability__`), primary responsibility, testing status, benchmark coverage, primary public API symbols, and verified statement coverage:

| Module | Purpose | Status | Tests | Benchmark | Public API | Coverage |
| :--- | :--- | :--- | :---: | :---: | :--- | :---: |
| `alert_sinks` | MDRAP External Alert Delivery Framework. | `stable` | ✓ | — | `RateLimiter, BaseHttpAlertSink, WebhookAlertSink` | 58% |
| `alerts` | Alerting & Notification System for MDRAP. | `beta` | ✓ | — | `AlertType, AlertStatus, Alert` | 85% |
| `analytics` | OHLCV and volatility analysis primitives | `beta` | ✓ | — | `OHLCVAggregator, SpreadAnalyzer, VolatilityTracker` | 99% |
| `api` | MDRAP Commercial Production REST & WebSocket API Service (FastAPI). | `beta` | ✓ | — | `HealthResponse, FeedRegisterRequest, FeedItem` | 87% |
| `archive` | Immutable Raw Event Archive module. | `stable` | ✓ | — | `RawArchive, replay` | 86% |
| `audit_format` | MDRAP Audit Trail Payload Canonicalization. | `stable` | ✓ | — | `audit_bytes_v1, audit_bytes_v2, compute_audit_hash` | 95% |
| `backtest` | Historical Backtesting Engine for MDRAP (Spec §18, §26). | `beta` | ✓ | — | `BacktestResult, BacktestEngine, load_events_from_store` | 95% |
| `bardb` | Persistent Multi-Timeframe OHLCV Bar Database & Roll-up Engine. | `beta` | ✓ | — | `Bar, BarAggregator, BarDatabase` | 94% |
| `bbo` | Synthetic Consolidated BBO (Best Bid & Offer / NBBO) Engine for MDRAP. | `stable` | ✓ | — | `ConsolidatedBBO, BBOEngine` | 97% |
| `benchmark` | Benchmarking Framework. | `stable` | ✓ | ✓ | `run_benchmark, save_result` | 22% |
| `chaos` | Chaos & Failure Injection Engine (MDRAP Spec Section 15). | `beta` | ✓ | — | `ChaosDrillResult, drop_source_window, ChaosEngine` | 96% |
| `chd` | CryptoHFTData historical provider: verified hourly files and lossless row access. | `beta` | ✓ | — | `urlopen, CHDError, MissingPartition` | 83% |
| `chd_history` | CHD trade normalization, snapshot-aware L2 reconstruction and atomic imports. | `beta` | ✓ | — | `timestamp_ns, OrderBook, iter_events` | 95% |
| `cli` | Market Data Reliability & Acceleration Platform — terminal CLI. | `beta` | ✓ | — | `cmd_run, cmd_benchmark, cmd_compare` | 43% |
| `client` | MDRAP Official Institutional Client SDK (Spec §18). | `beta` | ✓ | — | `MarketEvent, MDRAPClient, MDrapClient` | 72% |
| `columnar` | MDRAP Columnar Time-Series Storage & Ultra-Fast Analytical Engine (DuckDB & Parquet). | `beta` | ✓ | — | `ColumnarStore` | 86% |
| `config` | MDRAP Central Platform Configuration Manager. | `stable` | ✓ | — | `QualityConfig, BBOConfig, WatchdogConfig` | 96% |
| `config_loader` | MDRAP Hierarchical Configuration Loader. | `stable` | ✓ | — | `find_config_path, load_config, compute_config_hash` | 69% |
| `corporate_actions` | Corporate Actions Processor for MDRAP. | `experimental` | ✓ | — | `ActionType, CorporateAction, AdjustmentFactor` | 90% |
| `dashboard` | Live terminal dashboard (rich). Deliberately not a web UI: the spec | `beta` | ✓ | — | `render, Dashboard` | 79% |
| `databento_feed` | Databento Binary Encoding (DBN) High-Throughput Streaming Feed Engine for MDRAP. | `beta` | ✓ | — | `SymbolResolver, decode_dbn_record, SyntheticDBNGenerator` | 83% |
| `depth` | Consolidated Level-2 (L2) Market Depth Engine for MDRAP (Spec §18). | `beta` | ✓ | — | `DepthLevel, AggregatedLevel, VWAPSlice` | 92% |
| `export` | MDRAP Data Export Utility. | `stable` | ✓ | — | `export_data` | 67% |
| `exporter` | MDRAP Institutional Financial Model & Excel/CSV Exporter. | `beta` | ✓ | — | `MarketDataExporter` | 95% |
| `fastpath` | Python ctypes wrapper for MDRAP Native C Hot Path. | `stable` | ✓ | ✓ | `is_available, FastQualityEngine, get_quality_engine` | 84% |
| `features` | ML/AI Feature Store for MDRAP. | `beta` | ✓ | — | `sma, ema, rsi` | 93% |
| `feed_handler` | Unified High-Throughput Streaming Feed Supervisor for MDRAP. | `beta` | ✓ | — | `FeedProvider, FeedSupervisorConfig, StreamingFeedSupervisor` | 85% |
| `fix_engine` | FIX Protocol Engine for MDRAP. | `experimental` | ✓ | — | `FIXTag, FIXMessage, FIXSession` | 88% |
| `flow_tracker` | MDRAP Institutional Order Flow Tracker & Participant Attribution Engine. | `beta` | ✓ | — | `AggressorSide, FlowCategory, TradeFlowEvent` | 98% |
| `fx` | MDRAP Foreign Exchange (FX) Matrix and Multi-Currency Valuation Engine. | `beta` | ✓ | — | `FXMatrix, convert_currency` | 80% |
| `gateway` | Ingestion Gateway: Ingestion & Normalization Stage. | `stable` | ✓ | — | `IngestionGateway` | 95% |
| `gateway_tcp` | Raw TCP socket streaming gateway with line-delimited and binary framed protocols. | `beta` | ✓ | — | `TCPGateway` | 67% |
| `itch` | NASDAQ TotalView ITCH 5.0 Protocol Parser and Dissector for MDRAP. | `beta` | ✓ | — | `ITCHMessageType, ITCHMessage, SystemEvent` | 86% |
| `kafka_sink` | Decoupled Non-Blocking Kafka Publisher & Delivery Queue. | `stable` | ✓ | — | `KafkaSinkConfig, InMemoryKafkaProducer, BaseKafkaSink` | 71% |
| `live` | MDRAP Live Streaming Ingestion Layer (WebSockets). | `beta` | ✓ | — | `BinanceFeedHandler, CoinbaseFeedHandler, LiveStreamManager` | 72% |
| `manifest` | Package manifest & version information. | `stable` | ✓ | — | `Manifest` | 84% |
| `mbo` | Market-by-Order (MBO / Level 3) Engine for MDRAP. | `beta` | ✓ | — | `OrderSide, TimeInForce, Order` | 95% |
| `metrics` | Pipeline Metrics Collector. | `stable` | ✓ | — | `LatencyTracker, PipelineMetrics` | 85% |
| `models` | MDRAP Core Data Models. Pure stdlib dataclasses. | `stable` | ✓ | — | `QualityStatus, EventType, RawEvent` | 95% |
| `multicast_arbitrator` | MoldUDP64 / A/B Feed Line Arbitrator for MDRAP. | `beta` | ✓ | — | `ArbitrationStrategy, ArbitratedPacket, MoldUDP64Header` | 89% |
| `navigator` | Interactive System Navigator & Operational HUD for MDRAP. | `beta` | ✓ | — | `Screen, HelpEntry, MetricGauge` | 80% |
| `news` | Financial News & Macro Sentiment Ingestion Engine for MDRAP. | `beta` | ✓ | — | `SentimentLabel, NewsImpact, NewsEvent` | 88% |
| `options` | Options Analytics & Greeks Calculation Engine for MDRAP. | `experimental` | ✓ | — | `OptionType, OptionGreeks, BlackScholesPricer` | 93% |
| `pipeline` | Synchronous Processing Pipeline. | `stable` | ✓ | ✓ | `PipelineResult, Pipeline` | 80% |
| `polygon_feed` | Polygon.io WebSocket Streaming Market Data Client for MDRAP. | `beta` | ✓ | — | `PolygonChannel, PolygonEvent, PolygonFeedHandler` | 70% |
| `portfolio` | Real-time Portfolio & Position Risk Accounting Engine for MDRAP. | `beta` | ✓ | — | `Fill, Position, Portfolio` | 80% |
| `prometheus` | Prometheus Metrics Exporter & Metrics Scrape Adapter for MDRAP. | `stable` | ✓ | — | `PrometheusExporter` | 92% |
| `protocol` | Binary serialization protocol for MDRAP events (IPC & persistence). | `stable` | ✓ | — | `EventDecoder, encode_raw_event, decode_raw_event` | 96% |
| `protocols` | MDRAP Extension Protocols & Public Interfaces. | `stable` | ✓ | — | `FeedAdapter, StorageBackend, QualityRule` | 100% |
| `quality` | Data Quality Engine: 7 Core Validation Rules (MDRAP Spec Section 10). | `stable` | ✓ | ✓ | `QualityResult, QualityEngine` | 76% |
| `reconciliation` | Multi-Source Cross-Reconciliation Engine (MDRAP Spec Section 11). | `stable` | ✓ | ✓ | `ReconciliationConfig, Reconciler` | 98% |
| `research` | Quantitative Research & Analytics Workspace for MDRAP. | `experimental` | ✓ | — | `ResearchReport, QuantitativeResearchSuite` | 75% |
| `risk` | Real-Time Risk & Pre-Trade Limits Engine for MDRAP. | `experimental` | ✓ | — | `RiskLimitType, RiskAction, RiskRule` | 85% |
| `rules` | Quality validation rules registry & user bitmask allocation. | `stable` | ✓ | — | `register_rule, list_rules, clear_user_rules` | 71% |
| `sbe` | Simple Binary Encoding (SBE) High-Speed Parser for MDRAP. | `beta` | ✓ | — | `SBEType, SBEMessageType, SBEField` | 96% |
| `scheduler` | Enterprise Task Scheduler & Cron Supervisor for MDRAP. | `beta` | ✓ | — | `TaskStatus, ScheduledTask, TaskScheduler` | 85% |
| `security` | Authentication, RBAC, and Cryptographic Security Manager. | `stable` | ✓ | — | `Role, ClientEntitlement, SecurityManager` | 85% |
| `service` | High-Throughput Service Runtime & Decoupled Ingestion Fabric. | `beta` | ✓ | — | `MDRAPService` | 72% |
| `shm` | Shared Memory (IPC) Zero-Copy Streaming Transport for MDRAP. | `experimental` | ✓ | ✓ | `SHMRingBuffer, SHMProducer, SHMConsumer` | 79% |
| `simulator` | Synthetic Market Data Simulator (MDRAP Spec Section 9). | `stable` | ✓ | — | `MarketSimulator` | 90% |
| `storage` | Storage Layer: SQLite with Write-Ahead Logging & Parquet Columns. | `stable` | ✓ | ✓ | `Store` | 87% |
| `strategy_sdk` | Institutional Strategy & Algorithmic Execution SDK for MDRAP. | `experimental` | ✓ | — | `OrderSide, OrderType, OrderStatus` | 88% |
| `stresstest` | High-Scale Market Stress & Soak Testing Engine for MDRAP. | `beta` | ✓ | — | `StressProfile, StressScenario, StressResult` | 82% |
| `symbology` | Cross-Exchange Symbology & Security Master for MDRAP. | `beta` | ✓ | — | `AssetClass, SecurityIdentifier, SecurityDefinition` | 87% |
| `tca` | Transaction Cost Analysis (TCA) Engine for MDRAP. | `experimental` | ✓ | — | `BenchmarkType, TradeExecution, TCAAnalysis` | 96% |
| `term` | ANSI terminal styling, color codes, and ASCII formatting primitives. | `stable` | ✓ | — | `Color, Style, format_status` | 92% |
| `terminal_display` | Rich terminal visualization cockpit & live monitoring UI. | `beta` | ✓ | — | `LiveCockpit` | 79% |
| `trading_cli` | Trading Desk & Execution Operations CLI. | `beta` | ✓ | — | `cmd_positions, cmd_orders, cmd_risk` | 54% |
| `venues` | Venue Microstructure Specifications & Tick Rules for MDRAP. | `beta` | ✓ | — | `VenueType, FeeStructure, TickRule` | 83% |
| `vessel` | High-capacity in-memory order book & time-series cache. | `beta` | ✓ | — | `OrderBookSnapshot, TimeSeriesVessel` | 89% |
| `watchdog` | Feed Watchdog: Heartbeat, Staleness, and Auto-Isolation Engine. | `stable` | ✓ | — | `SourceState, WatchdogAlert, FeedWatchdog` | 100% |
| `workload_simulator` | Real-World Market Workload & Microstructure Stress Simulator. | `beta` | ✓ | — | `WorkloadScenario, WorkloadReport, WorkloadSimulator` | 71% |
| `ws_feed` | Resilient WebSocket Market Data Feed Engine. | `beta` | ✓ | — | `WebSocketConnectionConfig, WSFeedClient` | 54% |

---

## 4. Key Performance Baselines (Committed Artifacts)

Historical performance numbers from benchmark runs committed under `benchmarks/`:

- **Pure Python Baseline**: ~140,000 to 180,000 events/second (p99 latency < 12.5 µs).
- **Native C Fastpath (`_fastpath_native.dll`)**: ~850,000 to 1,200,000 events/second (p99 latency < 1.8 µs).
- **Shared Memory IPC (`shm.py`)**: 4,200,000+ events/second across single-producer single-consumer ring buffers.
- **SQLite WAL Write Batching**: 100,000 rows/second committed via `executemany` chunking.
