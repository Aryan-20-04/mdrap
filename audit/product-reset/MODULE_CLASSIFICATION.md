# MDRAP Product Reset — Module Classification Matrix

**Classification Policy**:
- **CORE**: Essential to the new product (reliability, provenance, ingestion, validation, replay, incident detection, audit, durability).
- **SUPPORTING**: Critical supporting infrastructure (storage, WAL, native fastpath kernel, SBE, config loader, logging, metrics).
- **OPTIONAL**: Potential future extension points (institutional exchange adapters, REST/WebSocket API server, IPC distribution).
- **DEPRECATED**: Outside the new product scope (trading strategies, quantitative execution, order matching, backtesting, generic retail charting). Retained for backward compatibility without breaking existing consumers.
- **UNVERIFIED**: Features claimed in documentation but lacking reproducible automated test coverage.

---

## Comprehensive Module Inventory

| Module / Path | Category | Classification | Strategic Action |
| :--- | :--- | :--- | :--- |
| `src/mdrap/ws_feed.py` | Ingestion | **CORE** | Standardize connector contract, heartbeat tracking, and telemetry interface. |
| `src/mdrap/gateway.py` | Ingestion | **CORE** | Maintain wire format normalization across all crypto venues. |
| `src/mdrap/models.py` | Core Data | **CORE** | Augment with first-class event provenance, data mode tags (`LIVE`, `REPLAY`, `SIMULATION`), and payload hashes. |
| `src/mdrap/provenance.py` | Trust Layer | **CORE** | Implement immutable lineage model, cryptographic payload hashing, and evidence tracking. |
| `src/mdrap/quality.py` | Validation | **CORE** | Core differentiator: explainable quality evaluation, sequence gap detection, stale data checks. |
| `src/mdrap/watchdog.py` | Reliability | **CORE** | Real-time venue health scoring, degraded state detection, and routing failover. |
| `src/mdrap/reconciliation.py`| Validation | **CORE** | Cross-venue price and book reconciliation with explicit fee/timing awareness. |
| `src/mdrap/incidents.py` | Observability | **CORE** | New module: structured incident model, anomaly timeline, and root-cause evidence logging. |
| `src/mdrap/replay.py` | Reconstruction | **CORE** | Transform into deterministic incident replay engine with bitwise state verification. |
| `src/mdrap/chaos.py` | Certification | **CORE** | Deterministic connector fault injection and compliance conformance testing. |
| `src/mdrap/storage.py` | Persistence | **SUPPORTING** | WAL-backed SQLite persistence, batched micro-commits, lineage preservation. |
| `src/mdrap/wal.py` | Durability | **SUPPORTING** | Append-only write-ahead log with segment CRC32C verification. |
| `src/mdrap/fastpath.py` | Performance | **SUPPORTING** | Vectorized C hot path quality kernel (`_fastpath_native.dll`), pure Python fallback parity. |
| `src/mdrap/shm.py` | IPC / Transport | **SUPPORTING** | Low-latency seqlock ring buffer for sidecar stream distribution. |
| `src/mdrap/sbe.py` | Serialization | **SUPPORTING** | High-throughput binary wire serialization for institutional feeds. |
| `src/mdrap/security.py` | Trust Layer | **SUPPORTING** | HMAC-SHA256 feed authentication, RBAC, Merkle audit chain verification. |
| `src/mdrap/config.py` | Configuration | **SUPPORTING** | TOML configuration loader, environment variables, validation gates. |
| `src/mdrap/alerts.py` | Notification | **SUPPORTING** | Alert routing to stdout, webhook, and monitoring systems on data-quality anomalies. |
| `src/mdrap/api.py` | API Server | **OPTIONAL** | Read-only FastAPI server for telemetry dashboards and incident query endpoints. |
| `src/mdrap/cme.py` / `eurex.py` | Ingestion | **OPTIONAL** | Institutional derivatives wire adapters (retained for future enterprise expansion). |
| `src/mdrap/af_xdp.py` | Transport | **OPTIONAL** | Linux kernel bypass socket driver (optional tier for bare-metal Linux). |
| `src/mdrap/bbo.py` | Microstructure | **SUPPORTING** | Consolidated Best Bid & Offer synthesis (essential for cross-venue anomaly detection). |
| `src/mdrap/depth.py` | Microstructure | **SUPPORTING** | Multi-venue Level-2 order book depth aggregation and crossed-book detection. |
| `src/mdrap/backtest.py` | Strategy | **DEPRECATED** | Move to optional/legacy tier; not part of market-data reliability platform. |
| `src/mdrap/risk.py` | Trading Risk | **DEPRECATED** | Pre-trade risk checks belong in trading execution engines, not data assurance sidecar. |
| `src/mdrap/tca.py` | Execution | **DEPRECATED** | Post-trade transaction cost analysis (retained in CLI for backward compatibility). |
| `src/mdrap/flow_tracker.py` | Trading Signals | **DEPRECATED** | Retail CVD and aggressive taker flow tracking (secondary to data assurance). |
| `packages/mdrap-strategies/` | Strategies | **DEPRECATED** | Market-making and momentum trading strategies (isolated in companion package). |
| `src/mdrap/exporter.py` | Export | **DEPRECATED** | Deprecated 1,528-line Excel model (pruned; replaced by lean CSV/Parquet/JSON `export.py`). |

---

## Strategic Module Boundaries

```text
                  +------------------------------------------+
                  |         EXTERNAL DATA SOURCES            |
                  | Binance, Bybit, Kraken, OKX, Databento   |
                  +--------------------+---------------------+
                                       |
                                       v
                  +------------------------------------------+
                  |       CONNECTOR RELIABILITY LAYER        | [CORE]
                  |      (ws_feed.py, gateway.py)            |
                  +--------------------+---------------------+
                                       |
                                       v
                  +------------------------------------------+
                  |      EVENT PROVENANCE & CAPTURE          | [CORE]
                  |      (provenance.py, models.py)          |
                  +--------------------+---------------------+
                                       |
                                       v
                  +------------------------------------------+
                  |     DATA QUALITY & ANOMALY ENGINE        | [CORE]
                  | (quality.py, watchdog.py, reconciler.py) |
                  +--------------------+---------------------+
                                       |
                         +-------------+-------------+
                         |                           |
                         v                           v
          +-----------------------------+ +-----------------------------+
          |     INCIDENT DETECTOR       | |    DURABLE LOG & AUDIT      | [CORE]
          |   (incidents.py, replay.py) | |   (wal.py, storage.py)      |
          +--------------+--------------+ +--------------+--------------+
                         |                               |
                         +---------------+---------------+
                                         |
                                         v
                         +-------------------------------+
                         |      ASSURANCE INTERFACE      | [CORE]
                         | (cli/assurance.py, api.py UI) |
                         +-------------------------------+
```
