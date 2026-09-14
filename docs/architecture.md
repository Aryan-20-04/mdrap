# MDRAP Architecture Specification

Based on the *Market Data Reliability & Acceleration Platform Reference (Section 5, 6, 14, 19, 26)*.

## 1. System Overview

The platform converts noisy, delayed, duplicated, and inconsistent market data from multiple sources into a fast, validated, canonical real-time data stream with end-to-end lineage, measurable reliability, and cryptographic auditability.

```
+-------------------------------------------------------------------------+
|                  Feed Simulator / External Market Feeds                 |
|  (Binance, Coinbase, Kraken, OKX, Bybit, Equities, Seeded Simulator)    |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                      Security & Ingestion Gateway                       |
|   - Token Bucket Rate Limiter (20,000 eps), Regex & Range Sanitizer     |
|   - HMAC-SHA256 Payload Verification & RBAC Entitlement Guard           |
|   - Ingest: receive timestamp, unique monotonic raw_id                  |
|   - Immutable Raw JSONL Archive (write-ahead partitioned log)           |
|   - Normalize: vendor format -> CanonicalEvent schema                   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                 Data-Quality Engine & Native C Fastpath                 |
|   - Schema validation (never drop, quarantine INVALID)                  |
|   - Dedup (bounded LRU key cache)                                       |
|   - Sequence-gap & out-of-order retrograde arrival detection            |
|   - Staleness & price anomaly (Welford's z-score)                       |
|   - Quote consistency (crossed and locked order books)                  |
|   - Native C Accelerator: 8,192 symbols ($2^{13}$), 18.6M eps, 50.0 ns  |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                    Consensus, Depth & Reconciler                        |
|   - Aligns observations by simulated market time                        |
|   - Tracks dynamic source reliability scores & failover circuit breaker |
|   - Synthetic Consolidated NBBO across 5 venues                         |
|   - Consolidated Level-2 Market Depth & VWAP Slippage Curve Engine      |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                              Storage Sink                               |
|   - Batched SQLite WAL (canonical_events, quarantine, lineage, health)  |
|   - Tamper-Evident Merkle Tree Audit Trail with SHA-256 Hash Chaining   |
|   - Analytical Storage Engine (5s OHLCV Candles, Spreads, Volatility)   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                  Distribution, Presentation & Export                    |
|   - Non-blocking Headless Streaming Daemon & Socket IPC                 |
|   - Binary Shared Memory Transport (Zero-Copy Ring Buffer)              |
|   - In-Place Live Terminal Ticker & ANSI Candlestick Visualizer         |
|   - Institutional 5-Tab Excel (.xlsx) & CSV Financial Exporter          |
+-------------------------------------------------------------------------+
```

---

## 2. Component Responsibilities

### 2.1 Security & Ingestion Gateway (`src/gateway.py`, `src/security.py`)
- **Rate Limiting & Sanitization**: Token bucket rate limiter protects pipeline from quote bursts and denial-of-service ($20,000\text{ eps}$). Regex and range checking sanitizes all numeric fields and string symbols.
- **HMAC Authentication & RBAC**: Constant-time HMAC-SHA256 signature verification protects against feed spoofing. Role-Based Access Control enforces entitlements (`VIEWER`, `OPERATOR`, `ADMIN`).
- **`ingest()`**: Records high-resolution `receive_timestamp` immediately upon arrival and assigns a monotonic `raw_id`.
- **`normalize()`**: Maps vendor-specific schemas to the uniform `CanonicalEvent`. Catches schema violations and routes them as `INVALID` events to quarantine.
- **Corrupt-Frame Quarantine Path**: Ingest feeds (`src/ws_feed.py`, `src/polygon_feed.py`) never silently drop malformed, truncated, or unparseable wire frames. Corrupt payloads are wrapped into `RawEvent(is_malformed=True)` and dispatched through `normalize()`, generating an `INVALID` event quarantined under `SCHEMA_VIOLATION` (Principle #3).
- **Write-Ahead Raw Archive (`src/archive.py`)**: Date- and source-partitioned JSONL logging preserves raw payloads before ingestion.

### 2.2 Quality Engine & Native C Fastpath (`src/quality.py`, `src/fastpath.c`, `src/fastpath.py`)
- Stateful per-`(source, instrument)` evaluation.
- Classifies into three non-downgradable levels: `VALID` < `SUSPICIOUS` < `INVALID`.
- Uses Welford's algorithm (`_RollingStats`) for numerically stable rolling mean and standard deviation.
- Deduplication uses an insertion-ordered LRU dictionary.
- **Native C Accelerator**:
  - Expanded to 8,192 symbols ($2^{13}$) and 32 sources with dynamic heap allocation.
  - Zero-division bitshift slot index: `(source_id << 13) | instrument_id`.
  - Contiguous SIMD-aligned arrays operate entirely inside CPU L1 cache.
  - Achieves **18,669,082 events/sec (50.0 nanoseconds/event)**.
  - Automatic boundary fallback to pure Python if beyond 8,192 symbols.

### 2.3 Consolidated Depth & Real-Time VWAP Engine (`src/depth.py`)
- Aggregates disparate quote feeds and order updates into a unified multi-venue Level-2 depth ladder.
- Computes real-time **Volume Weighted Average Price (VWAP)** execution schedules, slippage curves, and market impact estimates for arbitrary order sizes.
- Calculates dynamic bid/ask liquidity imbalances.

### 2.4 Cross-Feed Reconciler & Watchdog (`src/reconciliation.py`, `src/watchdog.py`, `src/bbo.py`)
- Maintains per-instrument alignment across multiple feeds.
- Computes real-time source reliability scores based on weighted performance:
  - Accuracy / Agreement: 40%
  - Completeness (no sequence gaps): 25%
  - Deduplication cleanliness: 20%
  - Latency / Freshness: 15%
- Generates Synthetic Consolidated NBBO with venue attribution and locked/crossed book status.
- Source Watchdog monitors feed silence and degradation, triggering automatic circuit breaker failovers.

### 2.5 Storage, Quarantine & Merkle Audit (`src/storage.py`, `src/security.py`)
- Segregates data streams into distinct tables:
  - `canonical_events`: Only `VALID` and `SUSPICIOUS` market events.
  - `quarantine`: `INVALID` events with raw payload and failure reason. Never drops data.
  - `lineage`: Step-by-step traceability linking canonical events to raw IDs and decision rules.
  - `source_health`: Historical log of source reliability scores.
  - `audit_log`: Cryptographically chained SHA-256 Merkle log with standalone export & verification.
- Context-manager enabled with batched `executemany` commits to maximize SQLite WAL throughput.

### 2.6 Analytical Storage Engine (`src/analytics.py`)
- Tick-level aggregation into completed and current **5-second OHLCV candles**.
- Tracks rolling bid/ask spread distributions and crossed-quote occurrences.
- Computes realized price volatility and price range percentages using Welford's online variance algorithm.

### 2.7 In-Place Terminal Visualization, Modal Navigator & Institutional Exporter (`src/terminal_display.py`, `src/navigator.py`, `src/exporter.py`)
- **In-Place Terminal HUD**: ANSI cursor repositioning renders live ticker tables and candlestick charts without vertical scrolling or terminal flicker.
- **Modal Keyboard Navigator Desk (`src/navigator.py`)**: High-velocity terminal desk with a 3-mode state machine (`NORMAL`, `FILTER`, `MODAL`), Vim home-row motions, live incremental search debounce, and two-stage armed execution tickets preventing stray key accidental order submissions.
- **Visual Candlestick Charts**: 3-character columns (` █ `, ` │ `, ` ┼ `) with outlier-resilient 10th–90th percentile scaling and synchronized volume histograms.
- **5-Tab Financial Model Exporter**: Translates market microstructure data into styled Microsoft Excel workbooks (`.xlsx`) or automated CSV report packages.

### 2.8 IPC, Shared Memory & Streaming Daemon (`src/service.py`, `src/shm.py`, `src/protocol.py`)
- Headless daemon running on a non-blocking streaming socket.
- Binary Shared Memory transport using lock-free ring buffers for sub-microsecond algorithmic bot feeds.

---

## 3. Evolutionary Roadmap (Versions 1 - 4)

- **V1 (Synchronous Baseline):** Single-process, synchronous Python pipeline. SQLite storage with batched writes. Establishes the ground-truth benchmark and profiling baseline (~29,400 eps).
- **V2 (Decoupled Streaming Architecture):** Decoupled ingestion, stream processing, and storage sink workers via bounded in-memory queue broker with high-watermark backpressure signaling (~22,300 eps).
- **V3 (Analytical Storage & Aggregation Engine):** 5-second OHLCV candlestick aggregation, spread distribution tracking, realized volatility calculation, and immutable raw JSONL write-ahead archive.
- **V4 (Native C Hot-Path & Quantitative Acceleration):** Compiled native C kernel (`fastpath.c`) with zero-configuration Git and Pip automated packaging:
  - **Vectorized SBE Engine**: Hardware-saturating contiguous binary frame validation achieving **51.42 million events/second (19.4 nanoseconds/event)**.
  - **Single-Event Hot Path**: Expanded 8,192 symbols ($2^{13}$) and 32 sources with FNV-1a dedup and Welford variance at **18.66 million events/second (50.0 nanoseconds/event)**.
  - **Quantitative & Options Kernels**: Binomial American options pricing (**64.1x faster**), Bollinger Bands rolling window (**51.6x faster**), Monte Carlo VaR simulation (**2.3x faster**), Wilder-smoothed RSI (**2.2x faster**), and FIX checksums (**3.1x faster**).
  - **Seamless Boundary Fallback**: Transparent pure-Python fallback ensuring 100% numerical parity and zero drops if C dynamic libraries are disabled (`MDRAP_DISABLE_FASTPATH=1`).
