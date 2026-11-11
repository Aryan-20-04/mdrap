# MDRAP Pilot Readiness Audit — Agent 4: Connector Reliability & Read-Only Safety

**Agent Role:** Lead Auditor for Exchange Connectors, Telemetry Contracts & Read-Only Invariants  
**Target:** `src/mdrap/ws_feed.py`, `src/mdrap/api.py`, `src/mdrap/security.py`  
**Evaluation Standard:** Institutional Connectivity Reliability & Strict Execution Isolation  
**Date:** 2026-11-11  

---

## 1. Executive Summary

Agent 4 conducted an architectural and security audit of MDRAP's exchange connectors and telemetry contracts. 

**Key Findings:**
1. **Strict Read-Only Safety Guarantee:** **VERIFIED**. The ingestion subsystem connects solely to public exchange WebSocket market-data streams (`@depth5`, `@bookTicker`, public ticker/order book channels). There are **zero** order submission, execution routing, account balance query, or trade cancellation endpoints in the live ingestion path. MDRAP cannot execute trades and neither requires nor accepts private trading API keys.
2. **Provider Telemetry Contract:** **VERIFIED**. `ProviderTelemetry` delivers structured institutional metrics (`state`, `feed_freshness_ms`, `sequence_gaps_count`, `dropped_count`, `wire_latency_us`).
3. **Hardcoded Freshness Threshold:** **CONFIRMED DEFECT (LOW SEVERITY)**. `get_provider_telemetry()` uses a hardcoded 10,000 ms threshold for detecting `ConnectorState.STALE`. Low-frequency feeds flap incorrectly between connected and stale.
4. **Queue Backpressure Tombstoning:** **VERIFIED**. Under subscriber lag or queue saturation, dropped events are not silently discarded; instead, explicit `TOMBSTONE_DROPPED` events are logged into the ring buffer and drop counters increment monotonically.

---

## 2. In-Depth Technical Verification

### 2.1 Read-Only Safety Proof
A full-codebase scan across `src/mdrap/` verified that:
- Connectors target public endpoints only:
  - Binance: `wss://stream.binance.com:9443/ws/{stream}`
  - Coinbase: `wss://ws-feed.exchange.coinbase.com`
  - Kraken: `wss://ws.kraken.com`
  - OKX: `wss://ws.okx.com:8443/ws/v5/public`
  - Bybit: `wss://stream.bybit.com/v5/public/spot`
- No outbound HTTP POST/PUT/DELETE requests for order management exist.
- Trading API keys (`api_secret`, `private_key`, `passphrase`) are neither required nor parsed.
- Ingestion operates in an isolated, passive read-only observation mode.

### 2.2 Telemetry Freshness Threshold Hardcoding
- **File / Lines:** [`src/mdrap/ws_feed.py:702-703`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ws_feed.py#L702-L703)
- **Code:**
  ```python
  if freshness_ms > 10_000.0:  # >10s without frame on active socket
      state = ConnectorState.STALE
  ```
- **Analysis:** A universal 10s threshold fails for:
  - Low-volume illiquid pairs (e.g. illiquid altcoins or exotic currency pairs that update once every 20-30s).
  - Ultra-high-frequency top-of-book feeds where a 500ms stall indicates a frozen upstream feed.
- **Remediation:** Introduce `stale_timeout_ms: float = 10_000.0` as a configurable parameter on `WebSocketFeedManager.__init__()`.

### 2.3 Bounded Queue & Zero-Silent-Drop Enforcement
- **File / Lines:** [`src/mdrap/ws_feed.py:596-646`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ws_feed.py#L596-L646)
- **Behavior:**
  When `self._queue.put_nowait(raw)` raises `queue.Full`, the oldest event is evicted, `self._drop_count` and `self._drop_counts[venue]` are incremented, and a tombstone record is recorded:
  ```python
  tombstone = self._create_drop_tombstone(venue, symbol, evicted=evicted)
  self._tombstones.append(tombstone)
  ```
  This strictly upholds Design Principle #3: *"Never silently discard bad data."*

---

## 3. Required Remediation for Pilot Readiness

1. **Configurable Freshness SLA**:
   Expose `stale_timeout_ms: float = 10_000.0` in `WebSocketFeedManager.__init__()` and pass it down to `get_provider_telemetry()`.
2. **Read-Only Affirmation in Startup Logs**:
   Explicitly print an operational security assurance banner on connector startup:
   `[ws_feed] Initialized read-only passive market data connectors. Order execution interfaces disabled.`
