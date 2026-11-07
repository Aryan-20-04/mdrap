# MDRAP — Data Source Inventory & Provider Architecture

**Classification**: Institutional Market Data Infrastructure Audit  
**Target Repository**: MDRAP (`Market Data Reliability & Acceleration Platform`)  
**Commit / Target**: Branch `audit/production-certification-remediation`  
**Date**: November 2026  

---

## 1. Executive Summary

This inventory documents every data source, market-data ingestion adapter, protocol unmarshaler, file loader, synthetic generator, and test fixture in the MDRAP repository. Each entry is classified according to its operational mode:
- **LIVE**: External real-time market data streamed over network sockets.
- **HISTORICAL**: Past exchange data parsed from static files or archive databases.
- **REPLAY**: Sequenced re-emission of recorded real-world binary or packet captures.
- **SIMULATION**: Synthetic generation (random walk, Geometric Brownian Motion, or algorithmic fuzzing).
- **TEST-ONLY**: Static fixtures and mock payloads confined exclusively to unit/integration testing suites.

---

## 2. Exhaustive Data Source Inventory

| Source Identifier | Source File Path | Operational Classification | Connection Mechanism | Authentication Required | Fail-Closed Behavior |
|---|---|---|---|---|---|
| **Coinbase WebSocket** | `src/mdrap/ws_feed.py` | **LIVE** | Full-duplex TLS WebSocket (`wss://ws-feed.exchange.coinbase.com`) | None (Public L2/Ticker) | Disconnects & alerts on socket drop; retries with exponential backoff. Zero mock fallback. |
| **Binance WebSocket** | `src/mdrap/ws_feed.py` | **LIVE** | Full-duplex TLS WebSocket (`wss://stream.binance.com:9443/ws/`) | None (Public Book Ticker & Depth) | Disconnects & retries with exponential backoff. Zero mock fallback. |
| **Kraken WebSocket** | `src/mdrap/ws_feed.py` | **LIVE** | Full-duplex TLS WebSocket (`wss://ws.kraken.com`) | None (Public Book Ticker) | Disconnects & retries with exponential backoff. Zero mock fallback. |
| **OKX WebSocket** | `src/mdrap/ws_feed.py` | **LIVE** | Full-duplex TLS WebSocket (`wss://ws.okx.com:8443/ws/v5/public`) | None (Public BBO/Tickers) | Disconnects & retries with exponential backoff. Zero mock fallback. |
| **Bybit WebSocket** | `src/mdrap/ws_feed.py` | **LIVE** | Full-duplex TLS WebSocket (`wss://stream.bybit.com/v5/public/spot`) | None (Public Orderbook L1) | Disconnects & retries with exponential backoff. Zero mock fallback. |
| **Polygon.io WebSocket** | `src/mdrap/polygon_feed.py` | **LIVE** / **SIMULATION** | TLS WebSocket (`wss://socket.polygon.io/stocks`, `crypto`, `forex`) | Yes (`POLYGON_API_KEY`) | **Strict Fail-Closed**: Raises `ValueError` if key missing unless `mock_mode=True` explicitly passed. |
| **Polygon Mock Stream** | `src/mdrap/polygon_feed.py` | **SIMULATION** | In-memory wire-format generator (`PolygonMockStream`) | None | Activated **only** when `mock_mode=True` is explicitly specified. Labeled in telemetry. |
| **Databento Live TCP** | `src/mdrap/databento_feed.py` | **LIVE** | Raw TCP socket (`live.databento.com:13000`) | Yes (`DATABENTO_API_KEY`) | **Strict Fail-Closed**: Raises `ValueError` if key and file_path missing unless `mock_mode=True`. |
| **Databento DBN File** | `src/mdrap/databento_feed.py` | **HISTORICAL** / **REPLAY** | Local binary disk read (`.dbn` files) | None (Local File) | Fails with `FileNotFoundError` or `IOError` if path invalid. |
| **Synthetic DBN Generator** | `src/mdrap/databento_feed.py` | **SIMULATION** | In-memory DBN binary packet builder (`SyntheticDBNGenerator`) | None | Activated **only** when `mock_mode=True` is explicitly set. Labeled in telemetry. |
| **Feed Simulator** | `src/mdrap/simulator.py` | **SIMULATION** | In-memory Geometric Brownian Motion generator (`FeedSimulator`) | None | Benchmark and CI test-only harness with fixed seed (`seed=42`). Never active in live mode. |
| **Workload Simulator** | `src/mdrap/workload_simulator.py` | **SIMULATION** | Multi-threaded synthetic market workload generator | None | Dedicated stress-testing tool. Explicit CLI invocation only. |
| **Historical Replay** | `src/mdrap/replay.py` | **REPLAY** | Timestamp-sequenced reader from IngestLog WAL / SQLite | None | Reads existing recorded journals. Labeled as replay in event lineage. |
| **Nasdaq ITCH Parser** | `src/mdrap/itch.py` | **REPLAY** / **LIVE** | Binary stream unmarshaler for Nasdaq TotalView ITCH 5.0 | None (Protocol level) | Decodes 40-byte ITCH messages. Quarantines malformed frames. |
| **CME MDP 3.0 SBE Parser** | `src/mdrap/cme.py` | **REPLAY** / **LIVE** | SBE binary parser for CME Simple Binary Encoding | None (Protocol level) | Validates template ID and schema version. |
| **PCAP Network Capture** | `src/mdrap/pcap.py` | **REPLAY** | Ethernet / IP / UDP packet unmarshaler for raw network captures | None (Raw pcap file) | Ingests network packet captures from solarflare / standard NICs. |
| **AIS Vessel Tracker** | `src/mdrap/vessel.py` | **HISTORICAL** / **LIVE** | Maritime AIS coordinate feed adapter | Optional API key | Ingests lat/long coordinates for commodity chokepoint tracking. |
| **Test Fixtures** | `tests/` | **TEST-ONLY** | Static JSON, CSV, binary blobs, and corrupt packet seeds | None | Isolated to pytest execution. Completely absent from production runtime paths. |

---

## 3. Data Flow Separation Architecture

MDRAP enforces strict architectural boundary separation between live external data, historical replay, and synthetic simulation:

```
[External Live Sockets]
  ├─ wss://ws-feed.exchange.coinbase.com  ──┐
  ├─ wss://stream.binance.com:9443/ws/     ──┼─► [ws_feed.py] ──────┐
  ├─ wss://ws.kraken.com                  ──┤                      │
  ├─ wss://socket.polygon.io (Auth Req)   ──┤                      ▼
  └─ live.databento.com:13000 (Auth Req)  ──┘                [gateway.py] (Normalize)
                                                                   │
[Historical / Replay]                                              ▼
  ├─ Recorded .dbn Files ──────────────────────────────────► [quality.py] (Validate)
  ├─ IngestLog WAL Journals ───────────────────────────────►       │
  └─ Network PCAP Dumps ───────────────────────────────────►       ▼
                                                             [bbo.py] (Reconcile)
[Synthetic Simulation (Isolated)]                                  │
  ├─ FeedSimulator (GBM, seed=42) ─────────────────────────►       ▼
  ├─ PolygonMockStream (mock_mode=True only) ──────────────► [ingestlog.py] (WAL CRC32)
  └─ SyntheticDBNGenerator (mock_mode=True only) ──────────►       │
                                                                   ▼
                                                             [storage.py] (SQLite WAL)
```

### Operational Isolation Guarantees:
1. **Zero Implicit Switching**: A failed network connection to an exchange NEVER triggers synthetic data generation.
2. **Telemetry Attribution**: Every generated `RawEvent` and `CanonicalEvent` carries an explicit `source` tag (e.g., `COINBASE`, `KRAKEN`, `BINANCE`, `MOCK-POLYGON`, `SIMULATOR`). Downstream trading engines can filter by source legitimacy.
3. **Mandatory Explicit Flags**: All mock generators require explicit programmatic flags (`mock_mode=True`) or explicit CLI flags (`--mock-feed`).
