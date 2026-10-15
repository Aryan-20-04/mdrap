# MDRAP Edition Boundaries & Package Separation Specification

**Document Version:** 1.0.0  
**Effective Date:** 2026-10-15  
**Target Release:** MDRAP v3.1.1  
**Applicability:** Core Single-Node Distribution (`mdrap-core`) and Companion Extensions

---

## 1. Architectural Charter & Objective

The MDRAP project delivers a fast, deterministic, single-node market data reliability and acceleration engine. To ensure long-term maintainability, rigorous formal auditability, and zero bloat, the codebase enforces strict **edition boundaries**:

1. **Core Single-Node Engine (`mdrap-core`):** 100% open-source under the permissive MIT License. Focused exclusively on normalizing, validating, persisting to Write-Ahead Log (WAL), and streaming market data via low-latency inter-process communication (IPC). Contains **zero** commercial or external framework dependencies.
2. **Operational Adapters & Integrations:** Pluggable feed handlers (ITCH, SBE, WebSockets), monitoring sinks (Prometheus, alerting), and local storage projections (SQLite, Parquet).
3. **Companion Analytics Packages:** Separate companion packages for specialized financial domain computations (derivatives pricing, transaction cost analysis, portfolio risk management, algorithmic strategy backtesting).

---

## 2. Module Classification Matrix

All modules within the repository are strictly categorized into one of three tiers:

| Tier | Module(s) | Role & Responsibility | License & Dependencies | Stability Contract |
|---|---|---|---|---|
| **Tier 1: Core Engine** | `models.py`<br>`engine.py`<br>`clock.py`<br>`ingestlog.py`<br>`shm.py`<br>`fastpath.py`<br>`fastpath.c`<br>`mdrap_core.c`<br>`quality.py`<br>`rules.py`<br>`rules.def`<br>`reconciliation.py`<br>`pipeline.py`<br>`projection.py`<br>`storage.py`<br>`config_loader.py`<br>`client.py`<br>`service.py`<br>`security.py` | Ingestion normalization, schema validation, monotonic sequencing, rule-based anomaly detection, atomic WAL durability, SPSC seqlock shared memory distribution, local query projections, and access authentication. | **100% MIT**<br>Python stdlib + optional native C compilation. Zero external dependencies. | **STABLE**<br>Semantic versioning guarantees; 100% backward compatibility within major releases. |
| **Tier 2: Adapters & Operations** | `itch.py`<br>`sbe.py`<br>`gateway.py`<br>`gateway_tcp.py`<br>`ws_feed.py`<br>`databento_feed.py`<br>`polygon_feed.py`<br>`simulator.py`<br>`pcap.py`<br>`prometheus.py`<br>`watchdog.py`<br>`alerts.py`<br>`alert_sinks.py`<br>`exporter.py`<br>`dashboard.py`<br>`terminal_display.py`<br>`columnar.py`<br>`archive.py`<br>`kafka_sink.py`<br>`bardb.py`<br>`bbo.py`<br>`depth.py` | Pluggable feed protocol decoders, transport gateways, terminal UI displays, metrics scrapers, and historical columnar archive sinks. | **100% MIT**<br>May optionally require `rich` (for terminal display) or protocol parsers. | **SUPPORTED**<br>Clean pluggable interfaces; degradation isolated to individual connectors. |
| **Tier 3: Companion & Deprecated** | `options.py`<br>`tca.py`<br>`risk.py`<br>`strategy_sdk.py`<br>`vessel.py`<br>`consensus.py` | Specialized downstream analytics: Black-Scholes options pricing, MiFID II / SEC 605 TCA execution scoring, portfolio VaR/CVaR risk, strategy execution templates, AIS marine tracking, and local consensus mocks. | **MIT Companion**<br>Migrated to standalone companion packages under `packages/` or deprecated. | **DEPRECATED / COMPANION**<br>Emits `DeprecationWarning` when imported directly from `mdrap`. |

---

## 3. Detailed Companion Package Separation

To resolve defect **F-13** (Core Scope Sprawl) and keep the core audit surface tight and auditable:

### 3.1 Derivatives Pricing (`options.py` -> `mdrap-options`)
- **Core Status:** Deprecated stub in `src/mdrap/options.py`.
- **Target Package:** `packages/mdrap-options` (`mdrap_options`).
- **Functionality:** Black-Scholes analytical formulas, binomial trees, American/European options pricing, implied volatility solving, and options chain aggregation.
- **Contract:** Communicates via standard `CanonicalEvent` quotes and trades.

### 3.2 Transaction Cost Analysis (`tca.py` -> `mdrap-analytics`)
- **Core Status:** Deprecated stub in `src/mdrap/tca.py`.
- **Target Package:** `packages/mdrap-analytics` (`mdrap_analytics`).
- **Functionality:** Microsecond execution slippage benchmarking against NBBO, SEC Rule 605/606 reporting, effective vs quoted spread quantification, and broker scorecards.
- **Contract:** Consumes reconciled BBO and trade streams from `mdrap-core` via SHM or SQLite journal.

### 3.3 Portfolio Risk Engine (`risk.py` -> `mdrap-analytics`)
- **Core Status:** Deprecated stub in `src/mdrap/risk.py`.
- **Target Package:** `packages/mdrap-analytics` (`mdrap_analytics`).
- **Functionality:** Historical, parametric, and Monte Carlo Value-at-Risk (VaR), Conditional VaR (Expected Shortfall), drawdown circuit breakers, and position concentration limits.
- **Contract:** Ingests post-validation tick series from `mdrap-core`.

### 3.4 Algorithmic Strategy SDK (`strategy_sdk.py` -> `mdrap-strategies`)
- **Core Status:** Deprecated stub in `src/mdrap/strategy_sdk.py`.
- **Target Package:** `packages/mdrap-strategies` (`mdrap_strategies`).
- **Functionality:** Base classes for trading strategies, backtesting harnesses, and order generation.
- **Contract:** Strategy subscribers connect to `mdrap.shm` or `mdrap.client` as passive consumers.

### 3.5 Vessel Tracking (`vessel.py` -> `mdrap-contrib-vessel`)
- **Core Status:** Deprecated stub in `src/mdrap/vessel.py`.
- **Target Package:** `packages/mdrap-contrib-vessel` (`mdrap_vessel`).
- **Functionality:** AIS maritime vessel navigation tracking, geofencing, and port arrival forecasting.
- **Contract:** Third-party domain application utilizing MDRAP's generic time-series pipeline.

### 3.6 Multi-Node Consensus (`consensus.py`)
- **Core Status:** Deprecated / Scoped to single-process simulation.
- **Target Package:** Retained solely for local integration testing. Multi-node cluster claims are explicitly de-marketed in accordance with F-01.

---

## 4. Architectural Boundaries & Extension Contract

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CORE ENGINE (mdrap-core)                        │
│                                                                        │
│   ┌──────────────┐      ┌─────────────────┐      ┌─────────────────┐   │
│   │ RawEvent     │─────>│ Engine          │─────>│ IngestLog (WAL) │   │
│   │ Ingestion    │      │ (Quality/Rules) │      │ (Durability)    │   │
│   └──────────────┘      └────────┬────────┘      └─────────────────┘   │
│                                  │                                     │
│                                  ▼                                     │
│                         ┌─────────────────┐                            │
│                         │ CanonicalEvent  │                            │
│                         │ Stream & SHM    │                            │
│                         └────────┬────────┘                            │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │  Passive Read Interface
                                   │  (SHM / Socket / SQLite)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 OPTIONAL / COMPANION EXTENSIONS                        │
│                                                                        │
│   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐ │
│   │   mdrap-options    │ │  mdrap-analytics   │ │  mdrap-strategies  │ │
│   │ (Black-Scholes/IV) │ │    (TCA & VaR)     │ │   (Execution SDK)  │ │
│   └────────────────────┘ └────────────────────┘ └────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

### Invariants:
1. **Zero Reverse Dependencies:** `mdrap-core` never imports, relies upon, or calls into any Tier 3 companion package.
2. **Pluggable Consumption:** Downstream packages interact with `mdrap-core` strictly through published data structures (`CanonicalEvent`, `RawEvent`, `QualityStatus`, `Reason`) or IPC transports (`SHMReader`, SQLite WAL queries, TCP sockets).
3. **Pure Python Fallback Independence:** The core engine runs cleanly on any standard Python 3.10+ runtime without requiring C compilers, third-party C++ libraries, or external companion wheels.

---

## 5. Licensing & Commercial Terms

1. **Permissive Licensing:** The entire `mdrap-core` codebase is distributed under the standard **MIT License**. Users and institutions are free to use, modify, embed, and redistribute without royalties or proprietary licensing constraints.
2. **No Secret Tracking:** Zero telemetry, metrics phone-home, license verification servers, or hardware dongles are present in the core distribution.
3. **Independent Reproducibility:** All build scripts, tests, benchmarks, and packaging configurations are self-contained and reproducible offline.
