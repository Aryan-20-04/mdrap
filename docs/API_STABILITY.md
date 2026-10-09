# MDRAP Public API Stability Policy

**Status:** Official Platform Contract  
**Document Revision:** 1.0.0  
**Effective Date:** 2026-09-24  
**Reference ADR:** [ADR 0005: Backbone Stability Policy](decisions/0005-backbone-stability-policy.md)

---

## 1. Executive Summary & Purpose

MDRAP (Market Data Reliability & Acceleration Platform) serves financial market infrastructure, algorithmic trading desks, and quantitative research teams. For developers building mission-critical execution algorithms, custom exchange adapters, or proprietary analytics on top of MDRAP, API predictability is paramount.

This document formally defines the **Stability Contract** across all public MDRAP interfaces, establishing clear guarantees regarding semantic versioning, breaking changes, deprecation cycles, and internal boundaries.

---

## 2. Stability Tiers

Every public class, protocol, function, and module in MDRAP belongs to exactly one of the following five tiers:

```
┌─────────────────────────────────────────────────────────────┐
│                         STABLE                              │  Zero breaking changes in minor/patch releases.
├─────────────────────────────────────────────────────────────┤
│                          BETA                               │  Functionally complete & tested; minor ergonomic evolution.
├─────────────────────────────────────────────────────────────┤
│                      EXPERIMENTAL                           │  Research & exploratory prototyping; no compatibility guarantees.
├─────────────────────────────────────────────────────────────┤
│                       DEPRECATED                            │  Marked for sunset; emits runtime DeprecationWarning.
├─────────────────────────────────────────────────────────────┤
│                        INTERNAL                             │  Private symbols (prefixed with `_`); subject to instant refactor.
└─────────────────────────────────────────────────────────────┘
```

### 2.1 STABLE
- **Guarantee:** Public APIs are strictly frozen. Zero breaking changes within a major version (`v1.x.x`).
- **Deprecation Requirement:** Any future change requires a formal deprecation period lasting at least one minor release cycle (`v1.X` -> `v1.X+1`), during which the deprecated symbol emits a `DeprecationWarning` and continues functioning.
- **Coverage Target:** $\ge 90\%$ automated unit and integration test coverage.
- **Performance Requirement:** Benchmarked regression gates prevent tail latency or throughput degradation.

### 2.2 BETA
- **Guarantee:** Functionally complete, production-tested, and actively monitored. Minor ergonomic adjustments may occur across minor releases to improve usability or safety.
- **Coverage Target:** $\ge 80\%$ test coverage.
- **Promotion to Stable:** Requires at least one production release cycle without architectural breaking changes and broad test verification.

### 2.3 EXPERIMENTAL
- **Guarantee:** Active research, algorithmic prototyping, or speculative hardware integration (e.g. FPGA, quantitative research models, exotic options math). Interfaces may change, be refactored, or be removed between minor versions without notice.
- **Isolation Principle:** Experimental code is strictly decoupled and must never dictate or destabilize the core market data pipeline.

### 2.4 DEPRECATED
- **Guarantee:** Retained for backward compatibility. Clearly documented in `CHANGELOG.md` with recommended migration path. Will be removed in the subsequent major version.

### 2.5 INTERNAL
- **Guarantee:** Non-contractual implementation details. Identified by leading underscores (`_name`), private methods, internal helper scripts, or unexported symbols. Never depend on internal symbols in external code.

---

## 3. Public API Classification Matrix

### 3.1 Core Canonical Data Models (`src/mdrap/models.py`)

| Symbol | Stability | Description |
| :--- | :--- | :--- |
| [`RawEvent`](../src/mdrap/models.py) | **`STABLE`** | Ingested event containing source timestamp, venue, sequence, payload |
| [`CanonicalEvent`](../src/mdrap/models.py) | **`STABLE`** | Validated, normalized, consensus event with quality bitmask & lineage |
| [`EventType`](../src/mdrap/models.py) | **`STABLE`** | Canonical enumeration (`TRADE`, `QUOTE`, `BBO`, `BOOK_SNAPSHOT`, `HEARTBEAT`) |
| [`QualityStatus`](../src/mdrap/models.py) | **`STABLE`** | Strict quality classification hierarchy (`VALID`, `SUSPICIOUS`, `INVALID`) |
| [`Reason`](../src/mdrap/models.py) | **`STABLE`** | Standard reason bitmask codes (`DUPLICATE`, `OUT_OF_ORDER`, `CROSSED_BOOK`, etc.) |

### 3.2 Extension Protocols & Interfaces (`src/mdrap/protocols.py`)

Third-party packages and plugins rely on these structural subtyping protocols:

| Interface | Stability | Contract Methods |
| :--- | :--- | :--- |
| [`FeedAdapter`](../src/mdrap/protocols.py) | **`STABLE`** | `open() -> None`, `__iter__() -> Generator[RawEvent]`, `close() -> None` |
| [`StorageBackend`](../src/mdrap/protocols.py) | **`STABLE`** | `write_canonical()`, `write_quarantine()`, `query_canonical()`, `close()` |
| [`QualityRule`](../src/mdrap/protocols.py) | **`STABLE`** | `evaluate(event) -> tuple[int, QualityStatus]` (User bitmask 32–63) |
| [`OutputSink`](../src/mdrap/protocols.py) | **`STABLE`** | `publish(event) -> None`, `flush() -> None`, `close() -> None` |
| [`AlertSink`](../src/mdrap/protocols.py) | **`STABLE`** | `deliver(alert) -> bool`, `name: str` |
| [`AuthProvider`](../src/mdrap/protocols.py) | **`STABLE`** | `authenticate(token) -> Optional[ClientEntitlement]` |

### 3.3 Pipeline & Processing Engines

| Engine | Stability | Module | Responsibility |
| :--- | :--- | :--- | :--- |
| `Pipeline` | **`STABLE`** | [`src/mdrap/pipeline.py`](../src/mdrap/pipeline.py) | Synchronous feed processing pipeline (gateway -> quality -> reconcile -> persist) |
| `QualityEngine` | **`STABLE`** | [`src/mdrap/quality.py`](../src/mdrap/quality.py) | 7 core statistical & structural quality rules |
| `Reconciler` | **`STABLE`** | [`src/mdrap/reconciliation.py`](../src/mdrap/reconciliation.py) | Multi-feed consensus and dynamic cross-reconciliation |
| `FeedWatchdog` | **`STABLE`** | [`src/mdrap/watchdog.py`](../src/mdrap/watchdog.py) | Source liveness monitoring, heartbeat tracking, and auto-isolation |
| `Store` | **`STABLE`** | [`src/mdrap/storage.py`](../src/mdrap/storage.py) | Persistent SQLite engine with WAL journaling & Merkle quarantine log |
| `BBOEngine` | **`STABLE`** | [`src/mdrap/bbo.py`](../src/mdrap/bbo.py) | Consolidated National Best Bid & Offer (NBBO) aggregation |
| `FastQualityEngine` | **`STABLE`** | [`src/mdrap/fastpath.py`](../src/mdrap/fastpath.py) | Native C accelerated ctypes validation hot path |

### 3.4 Client SDK & Distribution Services

| Interface | Stability | Module | Responsibility |
| :--- | :--- | :--- | :--- |
| `MDRAPClient` | **`BETA`** | [`src/mdrap/client.py`](../src/mdrap/client.py) | Zero-dependency institutional client library with gap recovery |
| `MarketEvent` | **`BETA`** | [`src/mdrap/client.py`](../src/mdrap/client.py) | Normalized client event model |
| `REST & WebSocket API` | **`BETA`** | [`src/mdrap/api.py`](../src/mdrap/api.py) | Commercial REST endpoints and real-time streaming WebSocket hub |
| `PrometheusExporter` | **`STABLE`** | [`src/mdrap/prometheus.py`](../src/mdrap/prometheus.py) | OpenMetrics / Prometheus scrape exposition |
| `DurableKafkaSink` | **`STABLE`** | [`src/mdrap/kafka_sink.py`](../src/mdrap/kafka_sink.py) | Decoupled background Kafka publication queue |

### 3.5 High-Speed Transport & Analytical Storage

| Component | Stability | Module | Responsibility |
| :--- | :--- | :--- | :--- |
| `SHMRingBuffer` | **`BETA`** | [`src/mdrap/shm.py`](../src/mdrap/shm.py) | Lockless zero-copy shared memory IPC ring buffer |
| `ColumnarStore` | **`BETA`** | [`src/mdrap/columnar.py`](../src/mdrap/columnar.py) | Embedded DuckDB vectorized analytics and Parquet archival |
| `ITCHParser` | **`BETA`** | [`src/mdrap/itch.py`](../src/mdrap/itch.py) | NASDAQ TotalView ITCH 5.0 binary feed dissector |
| `SBEParser` | **`BETA`** | [`src/mdrap/sbe.py`](../src/mdrap/sbe.py) | Simple Binary Encoding (SBE) parser |

### 3.6 Research, Modeling & Quantitative Strategies

| Module | Stability | Responsibility |
| :--- | :--- | :--- |
| `strategy_sdk` | **`EXPERIMENTAL`** | Algorithmic execution templates (Avellaneda-Stoikov MM, Whale Tracker) |
| `options` | **`EXPERIMENTAL`** | Black-Scholes valuation and Options Greeks engine |
| `risk` | **`EXPERIMENTAL`** | Pre-trade risk limits, margin checking, and draw-down kill switches |
| `backtest` | **`BETA`** | Historical event replay and execution simulation |
| `tca` | **`EXPERIMENTAL`** | Transaction Cost Analysis (TCA) and slippage benchmarking |
| `research` | **`EXPERIMENTAL`** | Jupyter workspace helpers and statistical exploratory tooling |

---

## 4. Semantic Versioning Protocol

MDRAP strictly adheres to [Semantic Versioning 2.0.0](https://semver.org/):

$$\text{Version} = \text{MAJOR}.\text{MINOR}.\text{PATCH}$$

1. **MAJOR (`X.0.0`)**: Incompatible API breaking changes to `STABLE` interfaces.
2. **MINOR (`0.X.0`)**: Backwards-compatible additions to `STABLE` APIs, promotions from `BETA` to `STABLE`, or changes to `EXPERIMENTAL` APIs.
3. **PATCH (`0.0.X`)**: Backwards-compatible bug fixes, performance optimizations, and documentation corrections. Zero public API changes.

---

## 5. Community Extension Safety Contract

If you are developing third-party plugins or integrations for MDRAP:
1. Only subclass or implement protocols defined in [`src/mdrap/protocols.py`](../src/mdrap/protocols.py).
2. Register custom validation rules using the [`@register_rule`](../src/mdrap/rules.py) decorator within the user bitmask range (bits 32–63).
3. Do not rely on unexported modules or private helper methods (`_...`).
4. Validate extensions against the official 9-gate quality protocol: `./scripts/check.sh`.
