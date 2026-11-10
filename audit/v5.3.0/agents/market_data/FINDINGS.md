# Market Data & Live Feed Integrity — Detailed Findings

## Executive Summary
Audited all market data ingestion paths: live WebSocket streams, REST polling connectors, historical replay, synthetic simulators, protocol parsers, and multi-venue BBO aggregation. Genuine live feeds exist for 5 crypto exchanges (`ws_feed.py`, `live.py`), but critical defects were uncovered in Databento TCP streaming fragmentation, unencrypted API key transmission, missing package dependencies, and fabricated L2 order book depth for equities.

## Categorized Findings

### CRITICAL
1. **F-MKT-01: Databento TCP Stream Fragmentation Desynchronization & Silent Record Corruption**:
   - `src/mdrap/databento_feed.py:520-534`: TCP chunk reads that slice a DBN record in half cause `consumed <= 0`.
   - The loop breaks immediately without buffering residual bytes (`chunk[offset:]`).
   - On the next socket read, bytes are parsed from offset 0, reading payload bytes as record headers.
   - **Impact**: Stream desynchronization and complete corruption under live TCP conditions.

2. **F-MKT-02: Plaintext TCP Transmission of API Key & Missing CRAM Handshake in Databento**:
   - `src/mdrap/databento_feed.py:560-584`: Raw unencrypted TCP socket on port 13000 sends `auth=API_KEY` in plaintext without TLS encapsulation.
   - Discards initial greeting banner without parsing cryptographic CRAM challenge.
   - **Impact**: API key exposure; immediate rejection by real Databento Live Gateways.

### HIGH
3. **F-MKT-03: Critical `websockets` Dependency Missing from `pyproject.toml`**:
   - `pyproject.toml:39-88` has `dependencies = []` and omits `websockets` from all optional/dev groups.
   - `src/mdrap/ws_feed.py` and `polygon_feed.py` depend on `websockets`.
   - **Impact**: Standard `pip install .` cannot stream live WebSockets; `ws_feed` silently refuses to stream, and `polygon_feed` silently falls back to synthetic mock data.

4. **F-MKT-04: Fabricated Level 2 Depth Ladders in `live.py` Yahoo Finance Equity Feed**:
   - `src/mdrap/live.py:845-863`: Yahoo Finance chart API returns only top-of-book consolidated prices.
   - Code manufactures 5 bids and 5 asks with artificial quantities (100, 200, 300, 400, 500) around the market price.
   - **Impact**: Fabricated depth quotes injected into downstream depth engines and order book imbalance models.

5. **F-MKT-05: Unbounded 20 Hz REST Polling Without HTTP 429 Rate-Limit Backoff in `live.py`**:
   - `src/mdrap/live.py:1032-1108`: Polls at 50 ms intervals (20 Hz) across up to 5 venues.
   - `_get_json` treats HTTP 429/503 as `None` without checking headers or entering backoff.
   - **Impact**: Exchange gateways ban client IP address within seconds; triggers silent synthetic fallback.

### MEDIUM
6. **F-MKT-06: Phase 4 Adapters Have No-Op Network Connection Implementations**:
   - `src/mdrap/adapters/base.py:378-385` defines `_do_connect()` as `pass`. Subclasses (`BinanceAdapter`, `KrakenAdapter`, etc.) do not override it.
   - **Impact**: Adapters are in-memory parsers, not transport adapters.

7. **F-MKT-07: Silent Fallback to Synthetic Mock Data in `PolygonFeedManager`**:
   - `src/mdrap/polygon_feed.py:516-523`: If `mock_mode=False` but `websockets` is missing, silently executes `PolygonMockStream` (100% synthetic).

8. **F-MKT-08: Zero Live Network Integration Testing**:
   - Across 304 test files, not a single test establishes a live network connection to external exchange endpoints.

### LOW / INFO
9. **F-MKT-09: Fragile Kraken Pair Formatting on 7+ Character Tickers**:
   - `src/mdrap/ws_feed.py:768-775`: Naive string slicing `[:-3]/[-3:]` formats `XBTUSDT` as `XBTU/SDT`.
10. **F-MKT-10: Exemplary BBO Provenance & Strict Quality Priority Hierarchy**:
    - `src/mdrap/bbo.py:187-252` and `src/mdrap/quality.py:240-244` rigorously track venue attribution and enforce `INVALID > SUSPICIOUS > VALID` priority.\n