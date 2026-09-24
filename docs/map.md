# MDRAP Architecture Map

This document serves as the canonical architectural map and reference for the module layout of the Market Data Reliability & Acceleration Platform (MDRAP). MDRAP employs a flat `src/` layout consisting of 72 Python modules (alongside native C acceleration sources and definition files). All Python modules import directly from one another (for example, `from models import CanonicalEvent` or `from storage import Store`) without nested namespace packages or heavyweight ORM/web frameworks. The platform adheres to strict data segregation across Raw, Canonical, Quarantined, and Derived analytics tiers, enforces an immutable quarantine-never-drop policy, prioritizes quality status evaluation (`INVALID` > `SUSPICIOUS` > `VALID`), and relies on deterministic monotonic identifiers generated via `itertools.count()`.

---

## Module Layers

The 72 Python modules in `src/` are structured across 12 distinct functional layers, categorized from low-level data ingest and canonical pipeline processing to analytical engines, delivery fabrics, and client interfaces.

### Core Pipeline (stable)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`models.py`](../src/models.py) | Canonical data types | `RawEvent`, `CanonicalEvent`, `EventType`, `QualityStatus`, `Reason`, `AssetClass` | `stable` |
| [`gateway.py`](../src/gateway.py) | Ingestion and normalization | `ingest()`, `normalize()`, `SchemaError` | `stable` |
| [`quality.py`](../src/quality.py) | Data quality evaluation engine | `QualityEngine`, `QualityConfig` | `stable` |
| [`reconciliation.py`](../src/reconciliation.py) | Cross-feed reconciliation | `Reconciler`, `ReliabilityTracker` | `stable` |
| [`pipeline.py`](../src/pipeline.py) | Pipeline orchestration | `Pipeline`, `tuned_gc` | `stable` |
| [`metrics.py`](../src/metrics.py) | Runtime metrics | `RunMetrics`, `CompactSampleBuffer`, `percentile` | `stable` |
| [`config.py`](../src/config.py) | Configuration dataclasses | `PlatformConfig`, `QualityConfig`, `PipelineConfig`, `StorageConfig` | `stable` |
| [`config_loader.py`](../src/config_loader.py) | YAML config loading | `load_yaml_config` | `stable` |
| [`rules.py`](../src/rules.py) | User-defined quality rules | `@register_rule`, `evaluate_user_rules` | `stable` |
| [`rules.def`](../src/rules.def) | C X-Macro quality rule definitions | Preprocessor table of validation rules | `stable` |
| [`protocols.py`](../src/protocols.py) | Extension Protocol interfaces | `StorageBackend`, `AuthProvider`, `QualityEvaluator`, `OutputSink` | `stable` |

### Storage (stable)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`storage.py`](../src/storage.py) | SQLite persistence engine | `Store` | `stable` |
| [`columnar.py`](../src/columnar.py) | DuckDB analytical queries | `ColumnarStore` | `beta` |
| [`archive.py`](../src/archive.py) | Raw event archival | `RawArchive`, `replay` | `stable` |
| [`bardb.py`](../src/bardb.py) | Bar/candle database | `BarDatabase` | `beta` |
| [`chd.py`](../src/chd.py) | CHD machine | `CHDMachine` | `beta` |
| [`chd_history.py`](../src/chd_history.py) | CHD history engine | `CHDHistoryEngine` | `beta` |

### Ingestion & Feed Handling (stable)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`adapters/__init__.py`](../src/adapters/__init__.py) | FeedAdapter Protocol + entry_point discovery | `FeedAdapter`, discovery functions | `stable` |
| [`adapters/template.py`](../src/adapters/template.py) | Template adapter implementation | Example adapter implementation | `stable` |
| [`feed_handler.py`](../src/feed_handler.py) | Streaming feed supervisor | `StreamingFeedSupervisor` | `stable` |
| [`simulator.py`](../src/simulator.py) | Synthetic feed simulator | `FeedSimulator`, `SimulatorConfig` | `stable` |
| [`ws_feed.py`](../src/ws_feed.py) | WebSocket feed manager for crypto venues | `WebSocketFeedManager` | `beta` |
| [`polygon_feed.py`](../src/polygon_feed.py) | Polygon.io feed manager | `PolygonFeedManager` | `beta` |
| [`databento_feed.py`](../src/databento_feed.py) | Databento feed manager | `DatabentoFeedManager` | `beta` |
| [`live.py`](../src/live.py) | Live market data connector | `LiveConnector` | `beta` |

### Security & Audit (stable)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`security.py`](../src/security.py) | RBAC, HMAC auth, API key management | `SecurityManager`, `Role`, `ClientEntitlement` | `stable` |
| [`audit_format.py`](../src/audit_format.py) | Merkle-chain audit hash computation | `compute_audit_hash` | `stable` |

### Delivery & Output (stable)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`shm.py`](../src/shm.py) | Shared memory ring buffer SPMC | `SHMWriter`, `SHMReader` | `stable` |
| [`protocol.py`](../src/protocol.py) | Binary wire protocol | `pack_tick_frame`, `unpack_tick_frame` | `stable` |
| [`service.py`](../src/service.py) | TCP streaming daemon | `MarketDataDaemon` | `stable` |
| [`gateway_tcp.py`](../src/gateway_tcp.py) | TCP gateway server | `TCPGatewayServer` | `beta` |

### API (beta)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`api.py`](../src/api.py) | FastAPI REST + WebSocket server | `create_app`, `AppState` | `beta` |

### Analytics & Quantitative (beta)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`analytics.py`](../src/analytics.py) | OHLCV, spread, volatility aggregation | `MarketAnalytics` | `beta` |
| [`bbo.py`](../src/bbo.py) | Best Bid/Offer engine | `BBOEngine`, `ConsolidatedBBO` | `stable` |
| [`depth.py`](../src/depth.py) | L2 order book consolidation | `ConsolidatedDepthEngine` | `beta` |
| [`features.py`](../src/features.py) | Technical indicators | `sma`, `ema`, `rsi`, `macd`, `bollinger_bands`, `atr` | `beta` |
| [`options.py`](../src/options.py) | BSM options pricing | `bsm_price`, `bsm_greeks`, `implied_volatility` | `beta` |
| [`risk.py`](../src/risk.py) | Risk analytics | `PortfolioRiskEngine`, `ReturnSeries`, `DrawdownCircuitBreaker` | `beta` |
| [`flow_tracker.py`](../src/flow_tracker.py) | Order flow analysis | `OrderFlowTracker`, `LeeReadyClassifier` | `beta` |
| [`backtest.py`](../src/backtest.py) | Backtesting engine | `BacktestEngine` | `beta` |
| [`tca.py`](../src/tca.py) | Transaction cost analysis | `TCAEngine`, `TCAMetrics`, `ExecutionRecord` | `experimental` ⚠️ |
| [`portfolio.py`](../src/portfolio.py) | Portfolio tracking | `PortfolioTracker` | `beta` |

> ⚠️ **`tca.py` compliance notice:** this module implements metrics shaped around SEC Rule 605/606 and MiFID II RTS 27/28 concepts (execution quality, price improvement, venue routing statistics). It has **not been reviewed by a securities compliance professional or counsel**. If you're building a real best-execution or regulatory reporting product on top of it, get that review before relying on its output for any compliance, audit, or regulatory-filing purpose — treat it as a starting implementation of the *shape* of these metrics, not a validated compliance engine.

### Research & Alternative Data (experimental)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`research.py`](../src/research.py) | Company research engine | `EdgarClient`, `CompanyProfile`, `FilingRecord` | `experimental` |
| [`news.py`](../src/news.py) | Financial news and sentiment | `NewsFeed`, `FinancialSentimentAnalyzer` | `experimental` |
| [`corporate_actions.py`](../src/corporate_actions.py) | Corporate actions engine | `CorporateActionsEngine` | `experimental` |
| [`vessel.py`](../src/vessel.py) | AIS vessel tracking | `VesselTracker`, `Vessel`, `Chokepoint` | `experimental` |

### Infrastructure & Native Acceleration (stable)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`fastpath.py`](../src/fastpath.py) | C FFI acceleration layer | `FastQualityEngine`, `NativeReplayBuffer`, `is_available` | `stable` |
| [`fastpath.c`](../src/fastpath.c) | C source for native acceleration | Fast validation, ring buffer, and Welford variance routines | `stable` |
| [`fastpath.pyi`](../src/fastpath.pyi) | Type stubs | Type annotations for native C module | `stable` |

### CLI & Terminal UI (beta)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`cli.py`](../src/cli.py) | Main CLI entry point | `main` | `beta` |
| [`trading_cli.py`](../src/trading_cli.py) | Trading CLI | `add_trading_parsers`, `cmd_backtest`, `cmd_risk` | `experimental` |
| [`navigator.py`](../src/navigator.py) | TUI navigator | `MDRAPNavigator` | `beta` |
| [`terminal_display.py`](../src/terminal_display.py) | Rich terminal rendering | ANSI/Rich renderers, tables, live tickers | `beta` |
| [`dashboard.py`](../src/dashboard.py) | Dashboard view | Summary cockpit and telemetry widgets | `beta` |
| [`term.py`](../src/term.py) | Console abstraction | Terminal screen, formatting, and cursor utilities | `stable` |

### SDK & Client Libraries (beta)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`client.py`](../src/client.py) | Python SDK client | `MDRAPClient` | `beta` |
| [`strategy_sdk.py`](../src/strategy_sdk.py) | Strategy development SDK | Quantitative strategy base classes and runner interfaces | `experimental` |

### Supporting Infrastructure (various)

| Module | Purpose | Key Exports | Stability |
|---|---|---|---|
| [`symbology.py`](../src/symbology.py) | Symbol resolution | `resolve_symbol` | `stable` |
| [`venues.py`](../src/venues.py) | Global venue definitions | Global venue definitions and metadata registry | `stable` |
| [`fx.py`](../src/fx.py) | FX currency conversion | `FXConverter` | `beta` |
| [`scheduler.py`](../src/scheduler.py) | Task scheduler | `Scheduler`, `ScheduledJob`, `CronParser` | `beta` |
| [`watchdog.py`](../src/watchdog.py) | Source health watchdog | `SourceWatchdog` | `stable` |
| [`alerts.py`](../src/alerts.py) | Alert engine | `AlertEngine` | `beta` |
| [`benchmark.py`](../src/benchmark.py) | Pipeline benchmarking | `run_benchmark` | `stable` |
| [`export.py`](../src/export.py) | CSV/JSON export utilities | Export utilities for ticks, bars, and anomalies | `stable` |
| [`exporter.py`](../src/exporter.py) | Market data exporter | `MarketDataExporter` | `beta` |
| [`manifest.py`](../src/manifest.py) | Build manifest generation | Build manifest generator and commit hash utilities | `stable` |
| [`chaos.py`](../src/chaos.py) | Chaos/fault injection | `ChaosEngine`, `ChaosInjector` | `beta` |
| [`stresstest.py`](../src/stresstest.py) | Stress testing | `stress_end_to_end`, `stress_gateway`, `stress_quality_engine` | `beta` |
| [`workload_simulator.py`](../src/workload_simulator.py) | Concurrent workload simulator | `ConcurrentWorkloadSimulator`, `UserArchetype`, `DeviceConfig` | `beta` |
| [`multicast_arbitrator.py`](../src/multicast_arbitrator.py) | A/B multicast feed arbitration | `ABFeedArbitrator` | `beta` |
| [`sbe.py`](../src/sbe.py) | SBE codec | Simple Binary Encoding (SBE) encoders and decoders | `experimental` |
| [`itch.py`](../src/itch.py) | ITCH 5.0 parser | `ITCHParser`, `ITCHOrderBookTracker` | `beta` |
| [`fix_engine.py`](../src/fix_engine.py) | FIX protocol engine | `FIXSession`, `FIXEngineServer` | `experimental` |
| [`mbo.py`](../src/mbo.py) | Market-by-Order book | `OrderBookMBO` | `beta` |

---

## Extension Points

MDRAP provides 5 primary extension points that allow external packages or user code to plug into the pipeline lifecycle without modifying core engine files:

| Extension Point | Protocol | Entry Point Group | Registration | Documentation |
|---|---|---|---|---|
| Feed Adapters | `FeedAdapter` | `mdrap.adapters` | `entry_points` | [docs/extending/feed-adapter.md](./extending/feed-adapter.md) |
| Quality Rules | — (decorator) | `mdrap.quality_rules` | `@register_rule(bit=N)` | [docs/extending/quality-rules.md](./extending/quality-rules.md) |
| Storage Backends | `StorageBackend` | `mdrap.storage_backends` | `entry_points` | [docs/extending/storage-backend.md](./extending/storage-backend.md) |
| Auth Providers | `AuthProvider` | `mdrap.auth_providers` | `entry_points` | [docs/extending/auth-provider.md](./extending/auth-provider.md) |
| Output Sinks | `OutputSink` | `mdrap.output_sinks` | `entry_points` | [docs/extending/output-sink.md](./extending/output-sink.md) |

---

## Dependency Graph

The following Mermaid flowchart displays core module relationships and dependencies across ingestion, validation, state reconciliation, persistence, and output distribution:

```mermaid
flowchart TD
    models["models.py"]
    gateway["gateway.py"]
    quality["quality.py"]
    reconciliation["reconciliation.py"]
    pipeline["pipeline.py"]
    storage["storage.py"]
    security["security.py"]
    api["api.py"]
    service["service.py"]
    shm["shm.py"]
    protocol["protocol.py"]
    bbo["bbo.py"]
    depth["depth.py"]
    adapters["adapters/"]
    feed_handler["feed_handler.py"]
    simulator["simulator.py"]
    
    gateway --> models
    quality --> models
    reconciliation --> models
    pipeline --> gateway
    pipeline --> quality
    pipeline --> reconciliation
    pipeline --> storage
    pipeline --> models
    api --> pipeline
    api --> storage
    api --> security
    api --> bbo
    api --> depth
    service --> pipeline
    service --> storage
    service --> security
    service --> shm
    service --> protocol
    feed_handler --> simulator
    feed_handler --> adapters
    bbo --> models
    depth --> models
```

---

## Stability Labels

MDRAP categorizes all modules using explicit stability tiers to clarify public API support and deprecation lifecycle commitments:

- **stable**: Public API will not break without a deprecation cycle. Safe to depend on for production deployments and third-party integrations.
- **beta**: API may change between minor versions. Functional, tested, and active in pipeline benchmarks, but interface contracts are not yet frozen.
- **experimental**: No stability guarantees. May be incomplete, undergoing rapid iteration, untested in production environments, or subject to redesign/removal.

Each module declares its stability tier at the module level:

```python
__stability__ = "stable"  # or "beta" | "experimental"
```
