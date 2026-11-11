# MDRAP Consolidated Architecture After Cleanup

## 1. Product Mission & Identity

**MDRAP — Market Data Reliability & Assurance Platform**
*"Know exactly what your trading system received, whether the data was trustworthy, what went wrong, and how to reproduce the incident."*

MDRAP sits as an independent, read-only reliability sidecar between financial market data providers and trading systems. It is NOT a trading execution platform, order router, maritime intelligence tracker, or retail terminal.

---

## 2. Core Functional Pipeline

```
  +-----------------------------------------------------------------------------------+
  |                             MARKET DATA INGRESS                                   |
  |  - WebSocket Connectors (Coinbase, Binance, Kraken, OKX, Bybit)                   |
  |  - Binary Feeds (ITCH / SBE / Direct Multicast UDP / Diagnostic PCAP Replay)      |
  |  - Native C SPSC Ring Buffer / POSIX & Windows Shared Memory (0600 permissions)   |
  +-----------------------------------------------------------------------------------+
                                           |
                                           v
  +-----------------------------------------------------------------------------------+
  |                                 GATEWAY TIER                                      |
  |  - Raw Ingest Log (Monotonic seq, SHA-256 payload hash, monotonic receive ts)     |
  |  - Payload Normalization to CanonicalEvent                                        |
  |  - Event Provenance Registration (Stable UUID, Parent SHA-256 Lineage Hash)       |
  +-----------------------------------------------------------------------------------+
                                           |
                                           v
  +-----------------------------------------------------------------------------------+
  |                            DATA QUALITY ENGINE (7 RULES)                          |
  |  1. Price Sanity (Static bounds, dynamic ticks)                                   |
  |  2. Crossed Book Detection (Bid < Ask)                                            |
  |  3. Latency & Clock Skew (Staleness SLA, future clock jump detection)             |
  |  4. Sequence Gap & Stale Packet Inspection                                        |
  |  5. Spike & Step Change Anomaly Filtering                                         |
  |  6. Volume & Size Sanity Checks                                                   |
  |  7. Strict Priority: INVALID > SUSPICIOUS > VALID (Never downgraded)              |
  +-----------------------------------------------------------------------------------+
                                           |
                                           v
  +-----------------------------------------------------------------------------------+
  |                       CROSS-VENUE RECONCILIATION & ARBITRAGE                      |
  |  - Cross-Exchange Corridor Verification (Binance vs Coinbase vs Kraken)           |
  |  - Outlier Arbitration & Consensus BBO Synthesis                                  |
  +-----------------------------------------------------------------------------------+
                                           |
                                           v
  +-----------------------------------------------------------------------------------+
  |                         INCIDENT & EVIDENCE SUBSYSTEM                             |
  |  - Anomaly Tombstoning & Incident Lifecycle Tracking                              |
  |  - Durable Forensic IngestLog (Grouped WAL, Torn-Tail Crash Recovery)              |
  |  - SQLite Canonical Store (Batch executemany, WAL mode, synchronous=NORMAL)       |
  |  - RFC 6962 Cryptographic Merkle Audit Tree (SHA-256 binary leaf prefix 0x00/0x01)|
  |  - Standalone Evidence Verification (`tools/verify_evidence.py`)                  |
  +-----------------------------------------------------------------------------------+
                                           |
                                           v
  +-----------------------------------------------------------------------------------+
  |                       OPERATIONAL CONSUMPTION & REPLAY                            |
  |  - Deterministic Incident Replay Engine (`mdrap replay`)                          |
  |  - Read-Only REST API & WebSocket Server (`src/mdrap/api.py`)                     |
  |  - Headless Operational CLI (`src/mdrap/cli/`)                                    |
  |  - Optional Output Sink: Kafka / Redpanda Checkpointed WAL Tailer                 |
  +-----------------------------------------------------------------------------------+
```

---

## 3. Retained Core Components

1. **`src/mdrap/gateway.py`**: Passive normalization gateway, feed adapters, raw-to-canonical conversion.
2. **`src/mdrap/provenance.py`**: Deterministic cryptographic event provenance, stable identity, SHA-256 payload & parent lineage hashing.
3. **`src/mdrap/quality.py`**: Institutional 7-rule data quality evaluation engine with strict status hierarchy.
4. **`src/mdrap/reconciliation.py`**: Cross-venue corridor verification, latency-aware synthetic consensus BBO.
5. **`src/mdrap/storage.py`**: SQLite persistent Store with batch executemany, WAL mode, monotonic indexing.
6. **`src/mdrap/ingestlog.py`**: Durable write-ahead log (WAL) with group commit, CRC32C frame checks, and torn-tail recovery.
7. **`src/mdrap/incidents.py`**: Incident lifecycle management, anomaly tombstoning, and exportable forensics.
8. **`src/mdrap/replay.py`**: Deterministic incident replay engine with multi-asset, multi-venue state reconstruction.
9. **`src/mdrap/security.py`**: RFC 6962 Merkle audit tree, second-preimage resistance, read-only AST enforcement, API authentication.
10. **`src/mdrap/chaos.py`**: Chaos drill engine (packet drops, sequence gaps, clock skews, bursts).
11. **`src/mdrap/watchdog.py`**: Feed health monitoring, silence detection, heartbeat SLA enforcement.
12. **`src/mdrap/fastpath.c` & `mdrap_core.c`**: Native C hot path kernel with 100% semantic Python fallback parity.
13. **`src/mdrap/shm.py`**: POSIX and Windows zero-copy shared memory IPC ring buffer.
14. **`src/mdrap/cli/`**: Headless operational CLI with Rich formatting and stdlib plain-text fallback.
15. **`src/mdrap/pcap.py`**: Diagnostic Libpcap 2.4 and MoldUDP64 dissector harness for wire-level parser verification.

---

## 4. Components Eliminated (Out-of-Scope)

1. **Trading Execution & Simulation**:
   - `src/mdrap/trading_cli.py`: Interactive desk, order router, and backtesting simulation.
   - Associated CLI commands: `trade`, `buy`, `sell`, `cancel`, `portfolio`, `pnl`, `backtest`.
2. **Terminal Navigation & Visual TUI**:
   - `src/mdrap/navigator.py`: 1,234 lines of modal keyboard desk navigation.
   - `src/mdrap/terminal_display.py`: 1,238 lines of curses-style sparklines and candlestick charts.
   - `src/mdrap/tui.py`: Textual TUI event loop.
3. **Peripheral Domain Intelligence**:
   - `src/mdrap/vessel.py`: Maritime AIS intelligence tracking (migrated to companion package).
   - `src/mdrap/options.py`: Black-Scholes derivatives pricing and Greeks valuation.
   - `src/mdrap/fx.py`: Hardcoded FX triangular arbitrage matrix.
   - `cmd_edgar` in `src/mdrap/cli/integrations.py`: SEC corporate filing mock.
4. **Non-Functional Networking Stubs**:
   - `src/mdrap/af_xdp.py`: AF_XDP kernel-bypass mock without OS integration.
5. **Redundant Root Shims**:
   - 93 forwarding stubs under `src/*.py`. All internal imports migrated to `from mdrap import ...`.

---

## 5. Security & Isolation Guarantees

1. **Read-Only Ingress**: AST-verified zero order-placement APIs (`send_order`, `place_order`, `cancel_order`, `modify_order`, `execute_order`).
2. **Memory Boundary**: SHM permissions locked to `0600` (user-only read/write).
3. **Forensic Immutability**: All canonical events link cryptographically to their raw wire bytes and parent events via RFC 6962 SHA-256 Merkle root.
4. **Crash Durability**: Torn-tail recovery safely truncates incomplete WAL records on restart without corrupting historical journal history.
