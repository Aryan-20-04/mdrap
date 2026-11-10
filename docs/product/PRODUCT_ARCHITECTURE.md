# MDRAP Product Reset — System Architecture & Design Specification

**Document Version**: 2.0 (Product Reset)  
**Lead Architect**: Agent 1 (Product Architecture & Strategic Scope)  
**Status**: APPROVED BASELINE  

---

## 1. Architectural Mission & Core Primitives

The MDRAP architecture is refocused as an **Independent Market Data Assurance Sidecar**.

### Core Primitives
1. **Raw Event Capture**: Store the untouched wire bytes and network metadata exactly as received over TLS/WebSocket/TCP.
2. **Event Identity & Provenance**: Generate a monotonic, deterministic identity and cryptographic payload hash (`SHA-256`) for every event.
3. **Canonical Normalization**: Transform raw vendor representations into a strict institutional schema (`QUOTE`, `TRADE`, `BOOK_SNAPSHOT`, `HEARTBEAT`).
4. **Data Quality & Anomaly Detection**: Evaluate every canonical event against institutional quality rules (sequence continuity, clock drift, price implausibility, crossed books) with strict status hierarchy (`INVALID > SUSPICIOUS > VALID`).
5. **Durable Incident Log**: Persist all anomalies, sequence gaps, and degraded feed states into a WAL-backed relational store.
6. **Deterministic Incident Replay**: Reconstruct historical order-book states at microsecond resolution and verify identical bitwise outcomes.

---

## 2. Logical Data-Flow Architecture

```text
+-------------------------------------------------------------------------------+
|                        EXTERNAL MARKET DATA PROVIDERS                         |
|           Binance | Bybit | Kraken | OKX | Databento | Polygon                |
+---------------------------------------+---------------------------------------+
                                        | (Encrypted TLS WebSockets)
                                        v
+-------------------------------------------------------------------------------+
|                    CONNECTOR RELIABILITY LAYER (Core)                         |
|   • Standardized Lifecycle Contract (STATE: CONNECTED, RECONNECTING, STALE)   |
|   • Sequence Tracking, Heartbeats, Clock Drift Calculation                    |
|   • Explicit Provenance Tag: LIVE vs. REPLAY vs. SIMULATION                   |
+---------------------------------------+---------------------------------------+
                                        | (RawEvent with Wire Payload)
                                        v
+-------------------------------------------------------------------------------+
|                     EVENT PROVENANCE & CAPTURE (Core)                         |
|   • Monotonic Global ID (evt-...)                                             |
|   • Wire Hash: SHA-256(raw_payload)                                           |
|   • Tri-Timestamp Vector: [Exchange Ts, Local Receive Ts, Processing Ts]      |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                      CANONICAL NORMALIZATION (Core)                           |
|   • Gateway normalizer mapping vendor types to CanonicalEvent                 |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                 DATA QUALITY & ANOMALY EVALUATION (Core)                      |
|   • Native C Kernel (_fastpath_native.dll) / Pure-Python Equivalence Baseline |
|   • Anomaly Taxonomy: SEQ_GAP, STALE_FEED, CLOCK_SKEW, SPREAD_ANOMALY         |
|   • Strict Hierarchy: INVALID (Quarantine) > SUSPICIOUS > VALID               |
+-------------------+---------------------------------------+-------------------+
                    |                                       |
                    v (Anomalies / State Changes)           v (All Events)
+---------------------------------------+   +-----------------------------------+
|       INCIDENT DETECTION (Core)       |   |      DURABLE STORAGE (Supporting) |
|   • Anomaly Aggregator                |   |   • SQLite (WAL Mode, fsync:      |
|   • Feed Degradation Watchdog         |   |     grouped_by_time)              |
|   • Root-Cause Incident Correlator    |   |   • Raw Frames, Lineage, Audit Log|
+-------------------+-------------------+   +-----------------+-----------------+
                    |                                         |
                    +--------------------+--------------------+
                                         |
                                         v
+-------------------------------------------------------------------------------+
|                    DETERMINISTIC INCIDENT REPLAY (Core)                       |
|   • Re-feed original raw payloads through historical normalizer               |
|   • Verify state hash matches original execution bit-for-bit                  |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                  ASSURANCE API & RELIABILITY DASHBOARD (Core)                 |
|   • Terminal Reliability Cockpit (Live Feed Health, Gap Monitor)              |
|   • REST & WebSocket Telemetry Endpoints (Read-Only)                          |
|   • Incident Investigation & Forensic Export (CSV / Parquet / JSON)           |
+-------------------------------------------------------------------------------+
```

---

## 3. What to Retain, Isolate, and Deprecate

### Retained Engineering Workhorses
- **C Hot Path Kernel (`fastpath.c`, `_fastpath_native.dll`)**: 506k EPS at 1.5 µs p50 evaluation speed.
- **Pure-Python Validation Engine (`quality.py`)**: 100% semantic and numerical parity guarantee for portability.
- **Storage Subsystem (`storage.py`, `wal.py`)**: High-throughput SQLite in WAL mode with grouped fsync and CRC32C segment verification.
- **Seqlock SHM Ring Buffers (`shm.py`)**: Zero-copy IPC for low-latency stream distribution without socket overhead.
- **WebSocket Feed Infrastructure (`ws_feed.py`, `gateway.py`)**: Live multi-venue async connectors for Binance, Bybit, Kraken, and OKX.
- **Cryptographic Merkle Audit Engine (`security.py`, `packages/mdrap-audit`)**: RFC 6962 compliant hash chains for tamper-evident data assurance.

### Deprecated / Isolated from Primary Assurance Scope
- **Trading Strategies (`packages/mdrap-strategies/`)**: Avellaneda-Stoikov market making, whale momentum, etc., isolated into separate optional packages.
- **Pre-Trade Risk Engine (`src/mdrap/risk.py`)**: Margin calculations, position limits, and credit controls belong in trading execution systems.
- **TCA & Execution Algorithms (`src/mdrap/tca.py`)**: Transaction cost analysis preserved for backward compatibility but removed from core CLI branding.
- **Generic Terminal Charts (`src/mdrap/cli/market.py`)**: Candlestick charts and indicators relegated to secondary utilities.

---

## 4. Public Assurance API & CLI Contracts

### Primary CLI Commands
```powershell
# Real-time multi-venue feed reliability cockpit
mdrap status --db data/mdrap.db

# Live assurance stream monitoring over WebSockets
mdrap live BTC/USD --ws --limit 50

# Consolidated BBO & Cross-Venue Arbitrage/Disagreement inspection
mdrap bbo BTC/USD

# Audit log cryptographic chain verification
mdrap audit --verify

# Deterministic incident reconstruction
mdrap replay --incident <INCIDENT_ID> --db data/mdrap.db

# Connector chaos & resilience certification drill
mdrap chaos run --target binance --drill packet_loss
```

### Public Python SDK Interface
```python
from mdrap.assurance import MarketDataAssuranceSidecar
from mdrap.models import LiveStreamConfig

# Initialize sidecar in read-only tap mode
sidecar = MarketDataAssuranceSidecar(
    venues=["BINANCE", "BYBIT", "KRAKEN", "OKX"],
    instruments=["BTC/USD", "ETH/USD"],
    db_path="data/assurance.db",
)

# Start background assurance thread
sidecar.start()

# Query real-time feed health
health = sidecar.get_feed_health()
print(health)

# Query recent data-quality incidents
incidents = sidecar.query_incidents(limit=10)
```
