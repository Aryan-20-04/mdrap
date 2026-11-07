# MDRAP — Mock & Synthetic Data Detection Audit Report

**Audit Classification**: Anti-Fabrication & Live Data Authenticity Verification  
**Auditor**: Independent Principal Market Data Infrastructure Auditor  
**Date**: November 2026  
**Target Repository**: MDRAP (`Market Data Reliability & Acceleration Platform`)  

---

## 1. Executive Summary

A comprehensive, code-level search was executed across every file in the MDRAP repository to identify, analyze, and classify all mechanisms of synthetic price generation, random walk simulation, test fixtures, static market data, and mock network services.

The primary objective was to ensure that **no synthetic, mock, or simulated data can be silently substituted for live production market data under any failure scenario** (such as missing credentials, network disconnection, or rate limiting).

---

## 2. Codebase Scan Results: Synthetic Data Generation

### 2.1 Geometric Brownian Motion & Random Price Generators

1. **`src/mdrap/simulator.py` (`FeedSimulator`)**
   - **Mechanism**: Generates tick-by-tick market events using pseudo-random Gaussian returns:
     $$\Delta P_t = P_{t-1} \cdot (\mu \Delta t + \sigma \sqrt{\Delta t} \cdot Z), \quad Z \sim \mathcal{N}(0, 1)$$
   - **Configuration**: Uses deterministic seeding (`SimulatorConfig(seed=42)`). Deliberately injects anomalous ticks (crossed quotes, sequence jumps, stale timestamps) at controlled rates (`price_anomaly_rate = 0.0005`).
   - **Classification**: **SIMULATION ONLY**.
   - **Operational Safety**: Strictly isolated to benchmarks (`benchmarks/`), unit tests (`tests/`), and offline validation drills. It is never invoked by `mdrap serve`, `ws_feed.py`, or live ingestion handlers.

2. **`src/mdrap/polygon_feed.py` (`PolygonMockStream`)**
   - **Mechanism**: Generates authentic Polygon wire-format JSON strings using `self.rng = random.Random(seed)`. Prices evolve via Gaussian random percentage shifts: `pct = self.rng.gauss(0.0001, 0.001)`.
   - **Classification**: **EXPLICIT SIMULATION / REMEDIATED**.
   - **Operational Safety**:
     - *Previous Vulnerability*: Lines 442 previously allowed `self.mock_mode = mock_mode or not self.api_key`, silently switching to `PolygonMockStream` if `POLYGON_API_KEY` was missing.
     - *Verified Remediation*: Remediated in commit `509a8e3`. Line 442 now enforces:
       ```python
       if not mock_mode and not self.api_key:
           raise ValueError(
               "POLYGON_API_KEY is required when mock_mode is False. "
               "To stream synthetic market data for testing, explicitly specify mock_mode=True."
           )
       ```
     - Silent mock data fallback is **permanently eliminated**.

3. **`src/mdrap/databento_feed.py` (`SyntheticDBNGenerator`)**
   - **Mechanism**: Builds binary Databento DBN packet chunks (MBP-1, MBP-10, and Trade records) packed via `struct.pack`. Prices vary around base prices with random integer steps.
   - **Classification**: **EXPLICIT SIMULATION / REMEDIATED**.
   - **Operational Safety**:
     - *Previous Vulnerability*: Line 445 previously set `self.mock_mode = mock_mode or (not self.api_key and not file_path)`.
     - *Verified Remediation*: Remediated in commit `509a8e3`. Line 445 now enforces:
       ```python
       if not mock_mode and not self.api_key and not file_path:
           raise ValueError(
               "DATABENTO_API_KEY or file_path is required when mock_mode is False. "
               "To stream synthetic market data for testing, explicitly specify mock_mode=True."
           )
       ```
     - Silent binary mock generation is **permanently eliminated**.

4. **`src/mdrap/workload_simulator.py` (`MarketWorkloadSimulator`)**
   - **Mechanism**: Multithreaded synthetic stress generator emitting bursts of buy/sell orders and quotes to evaluate engine backpressure.
   - **Classification**: **TEST / BENCHMARK HARNESS**.
   - **Operational Safety**: Requires manual execution via `mdrap stress` or dedicated benchmark scripts. Completely inert during production operations.

---

## 3. Analysis of External Live Feeds (`src/mdrap/ws_feed.py`)

The persistent multi-venue WebSocket engine (`WebSocketFeedManager`) connects directly to public cryptocurrency exchange WebSockets:
- **Binance**: `wss://stream.binance.com:9443/ws/{stream}`
- **Coinbase**: `wss://ws-feed.exchange.coinbase.com`
- **Kraken**: `wss://ws.kraken.com`
- **OKX**: `wss://ws.okx.com:8443/ws/v5/public`
- **Bybit**: `wss://stream.bybit.com/v5/public/spot`

### Audit of `ws_feed.py` for Hidden Mock Logic:
- **Zero Synthetic Generators**: `ws_feed.py` does not contain any random number generators (`random`), Gaussian drift functions, or synthetic frame builders.
- **Genuine Frame Parsing**: Frames are parsed directly from network wire payloads:
  - `parse_binance_frame`: Unpacks raw JSON `@bookTicker` and `@depth5` frames.
  - `parse_coinbase_frame`: Unpacks genuine JSON ticker and snapshot frames with atomic exchange sequence numbers.
  - `parse_kraken_frame`: Unpacks Kraken's native nested list format (`[channelID, {"b": [price, volume, timestamp], "a": [...]}, ...]`).
  - `parse_okx_frame`: Unpacks OKX's native `books5` JSON frame structure.
  - `parse_bybit_frame`: Unpacks Bybit's v5 spot orderbook payloads.
- **Failure Mode**: When network drops occur, the engine triggers exponential backoff reconnects (`backoff = min(backoff * 2.0, 30.0)`). It **never** falls back to fake market data.

---

## 4. Verification of Hardcoded Fixtures

| File Location | Hardcoded Content | Classification | Justification |
|---|---|---|---|
| `tests/fixtures/` | Static JSON packets, truncated DBN bytes | **TEST ONLY** | Standard deterministic regression test vectors. Not imported by `src/mdrap/`. |
| `src/mdrap/venues.py` | Exchange MIC codes, currency mappings, tick sizes | **METADATA** | Static regulatory exchange metadata (ISO 10383 MICs, MiFID II tick sizes). |
| `src/mdrap/vessel.py` | Geographic chokepoint coordinates (Suez, Panama, Hormuz) | **STATIC GEOMETRY** | Geographic latitude/longitude polygons for maritime spatial filtering. |

No hardcoded price feeds or fake market streams exist in the core processing engine.

---

## 5. Mock Data Risk Verdict

| Risk Category | Pre-Audit Status | Remediated Status | Verification Evidence |
|---|---|---|---|
| **Silent Mock Fallback on Missing Polygon Key** | VULNERABLE | **FIXED / ZERO RISK** | `test_polygon_feed_manager_missing_credentials_fails_explicitly` passes cleanly; raises `ValueError`. |
| **Silent Mock Fallback on Missing Databento Key** | VULNERABLE | **FIXED / ZERO RISK** | `test_databento_feed_manager_missing_credentials_fails_explicitly` passes cleanly; raises `ValueError`. |
| **CLI Implicit Mock Ingestion** | VULNERABLE | **FIXED / ZERO RISK** | `cli/market.py` rejects live streaming commands without credentials unless `--mock-feed` is passed. |
| **Crypto WebSocket Synthetic Fallback** | SAFE | **SAFE / ZERO RISK** | No synthetic generators exist in `ws_feed.py`. Network drop triggers backoff retry only. |
| **Engine Core Data Injection** | SAFE | **SAFE / ZERO RISK** | Core engine (`engine.py`, `quality.py`) processes input events agnostically without synthetic creation. |

**Final Finding**: MDRAP contains zero undetected or silent synthetic data fallback mechanisms. All simulation engines are explicitly isolated, strictly labeled, and require deliberate operator activation.
