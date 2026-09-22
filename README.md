# Market Data Reliability & Acceleration Platform (MDRAP)
### The reliability and audit layer between raw market data feeds and everything else

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-690%2F690%20passing%20(749%20total)-brightgreen.svg)](tests/)
[![Hot Path Latency](https://img.shields.io/badge/hot--path-19.4%20ns%20%7C%2051.4M%20eps-orange.svg)](src/fastpath.c)
[![Architecture](https://img.shields.io/badge/architecture-V1%20%7C%20V2%20%7C%20V3%20%7C%20V4%20C--Fastpath-purple.svg)](docs/architecture.md)
[![User Guide](https://img.shields.io/badge/manual-Operator%20%26%20User%20Guide-teal.svg)](docs/USER_GUIDE.md)
[![Dependencies](https://img.shields.io/badge/dependencies-zero%20mandatory-success.svg)](requirements.txt)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MDRAP ingests multiple live market data feeds, cross-reconciles them, flags anomalies with explainable reason codes, and produces a cryptographically auditable record of every data quality decision. It sits *before* your trading engine, database, or research code.

| Feature | MDRAP | QuestDB | NautilusTrader | kdb+ |
|---------|-------|---------|----------------|------|
| Cross-source reconciliation | ✅ Built-in | ❌ | ❌ | ❌ |
| Statistical quality scoring | ✅ 7-rule engine | ❌ | ❌ | Manual |
| Explainable reason codes | ✅ Per-event | ❌ | ❌ | ❌ |
| Cryptographic audit trail | ✅ Signed hash chain (v2) | ❌ | ❌ | ❌ |
| Tick-level storage | Via SQLite | ✅ Purpose-built | ✅ Parquet catalog | ✅ Purpose-built |
| Strategy execution | ❌ Not its job | ❌ | ✅ Purpose-built | Via q |
| `pip install` + CLI | ✅ | ❌ (Java) | ✅ | ❌ (Commercial) |

> MDRAP is not a replacement for QuestDB, NautilusTrader, or kdb+ — it's the layer you run *before* them, so you can trust what you're trading or backtesting on.

### 🤖 Built for AI Agents & Automated Pipelines
Every data-producing command supports `--json` for direct consumption by AI agents (Claude Code, Cursor, Codex) and automated research scripts with zero text-scraping:
```bash
mdrap bbo AAPL --json          # Real-time consolidated NBBO in structured JSON
mdrap status --json            # Pipeline health, queue counts, and database metrics
mdrap analytics spread --json  # Bid/ask spread statistics & crossed quotes
mdrap retention --json         # Pruning stats and WAL compaction summary
mdrap q latest AAPL            # Canonical tick records with provenance metadata
```

📖 **Complete Documentation**: See the [**Comprehensive Operator & User Manual**](docs/USER_GUIDE.md) for full syntax, flags, hotkeys, and role-based workflows.

---

## High-Level Architecture

```mermaid
flowchart TD
    subgraph INGESTION ["1. Market Ingestion & Direct Streaming Feeds"]
        POLY["Polygon.io WebSocket<br/>US Equities & Crypto Q/T/AM"]
        DBN["Databento Binary DBN<br/>CME/Nasdaq MBP-1/10 & Trades"]
        B["Binance / Coinbase / Kraken<br/>Crypto WebSockets & REST"]
        SIM["Deterministic Feed Simulator<br/>Seeded Faults & Injections"]
        FSUP["Streaming Feed Supervisor<br/>Thread-Safe Low-Contention Queue"]
    end

    subgraph SECURITY ["2. Security & Gatekeeper (Spec §19)"]
        RL["Token Bucket Rate Limiter<br/>20,000 eps per IP/Key"]
        SAN["Regex & Range Payload Sanitizer"]
        HMAC["HMAC-SHA256 Signature Verification<br/>Constant-Time Digest"]
        RBAC["RBAC Entitlement Guard<br/>VIEWER / OPERATOR / ADMIN"]
    end

    subgraph PIPELINE ["3. Validation, Acceleration & Consensus Pipeline"]
        GW["Gateway & Normalization<br/>RawEvent -> CanonicalEvent"]
        QE["7-Rule Quality Engine<br/>Schema, Dedup, Gap, Order, Stale, Crossed, 6σ"]
        FP["Native C Hot Path Accelerator<br/>24.4 ns batch / ~50 ns single | ~16 µs Python pipeline | ~0.8 ms durable"]
        WD["Source Watchdog & Failover Circuit Breaker<br/>Silence & Degradation Monitoring"]
        BBO["Synthetic Consolidated BBO<br/>5-Venue Multi-Exchange NBBO"]
        DEPTH["Consolidated L2 Order Book<br/>Multi-Venue Depth Aggregation & VWAP Curves"]
    end

    subgraph STORAGE ["4. Columnar & Batched Storage, Archive & Audit (Spec §14, §19, §26)"]
        CAN[("canonical_events<br/>WAL SQLite Batch")]
        QUAR[("quarantine<br/>Every Drop Counted & Reported")]
        LIN[("lineage<br/>Transformation Lineage Proof")]
        AUD[("audit_log<br/>Signed Hash Chain with External Anchors")]
        ARC[["Immutable Raw JSONL Archive<br/>Write-Ahead Partitioned Log"]]
        COL[("DuckDB Columnar Store<br/>SIMD Resampling & Parquet Export")]
    end

    subgraph PRESENTATION ["5. Presentation, IPC & Institutional Export"]
        DAEMON["Headless Streaming Daemon<br/>Non-blocking Socket IPC"]
        SHM["Binary Shared Memory Transport<br/>Seqlock Ring Buffer (v3 Layout)"]
        LIVE["In-Place Live Terminal Ticker<br/>Cursor-Repositioned Rich HUD"]
        CHART["Visual Candlestick Terminal Chart<br/>Unicode Wicks & Outlier Percentile Scaling"]
        EXCEL["Institutional 5-Tab Excel Exporter<br/>XLSX Financial Model & CSV Packages"]
    end

    POLY & DBN & B & SIM --> FSUP --> ARC
    ARC --> RL --> SAN --> HMAC --> RBAC --> GW
    GW --> QE
    QE <--> FP
    QE --> BBO & DEPTH
    QE --> WD
    BBO & DEPTH --> DAEMON & SHM
    QE --> CAN & QUAR & LIN & AUD
    CAN --> COL
    BBO & DEPTH & COL --> LIVE & CHART & EXCEL
```

---

## Key Platform Capabilities

### 1. 7-Rule Data Quality Engine (Spec §7)
- **Structural Schema Validation**: Rejects malformed JSON, invalid event types, and non-finite or missing attributes.
- **Exact & Sliding-Window Deduplication**: 64-bit sequence bitmaps for sequenced feeds and 2-generation sliding tables for unsequenced feeds.
- **Monotonic Sequence Gap Detection**: Detects missing exchange packets with candidate resynchronization.
- **Out-of-Order Sequencing**: Catches retrograde arrival events within bounded sliding sequence windows.
- **Timestamp Staleness Evaluation**: Flags lagging feeds exceeding max latency thresholds.
- **Crossed Quote Detection**: Flags invalid book states where $\text{Bid} \ge \text{Ask}$.
- **Statistical Price Sanity Checks**: Evaluates sudden price jumps ($>6\sigma$) using Welford's online variance algorithm with relative $\sigma$-floor and regime-shift re-seeding.
- **Strict Quality Priority**: Non-downgradable status progression: `INVALID` > `SUSPICIOUS` > `VALID`. Quarantines bad data; **every drop is counted and reported**.

### 2. Native C Hot-Path Accelerator (`fastpath.c`)
- Pure C implementation compiled into native shared library (`_fastpath_native.dll`).
- **Lazy 96-Byte Slot Allocation**: Contiguous memory indexing with bounded chunk pools and zero startup RSS bloat.
- **Measured Latency Profile**: **24.4–37.5 ns/event** in sequenced batch C kernel; **~50 ns** single evaluation; **~15.3–16 µs** Python in-memory pipeline; **~0.8 ms** durable SQLite WAL commits. See [Benchmark Methodology](docs/benchmark-methodology.md) for stage-by-stage measurement proofs and committed JSON reports.
- Seamless, transparent boundary fallback to pure Python if instrument universe exceeds 8,192 symbols.

### 3. Direct High-Throughput Streaming Feed Handlers (`src/polygon_feed.py`, `src/databento_feed.py`, `src/feed_handler.py`)
- **Polygon.io WebSocket Connector**: Streams high-frequency US Equities and Crypto quotes (`Q`), trades (`T`), and aggregate bars (`AM`) with API key authentication, multiplexed subscription channels, exponential backoff reconnection, and built-in offline wire-format mock generators.
- **Databento Binary Encoding (DBN) Ingestion**: Sub-microsecond binary record decoding using `struct.Struct` with C-struct layouts for Databento DBN formats (`MBP-1`, `MBP-10`, `TradeMsg`), nanosecond UTC epoch timestamps, fixed-point price scaling ($10^9$), dynamic symbol resolution, and live TCP / `.dbn` file / synthetic binary packet streaming.
- **Unified Streaming Feed Supervisor**: Coordinates multiple streaming providers into a bounded, low-contention queue with ring eviction to guarantee real-time latency and zero stale queue backlog. Ingestion telemetry tracks throughput (eps), dropped frames, and provider health.

### CHD Historical Data

[CryptoHFTData historical integration](docs/CHD.md) provides symbol discovery,
UTC interval planning, resumable Parquet downloads for six native datasets,
and atomic trade/order-book imports with replay archives and file provenance.
Install with `pip install -e '.[chd]'`, then run `mdrap historical providers`.
For a runnable Python walkthrough with charts, open the
[CHD historical data notebook](examples/03_chd_historical_data.ipynb).

```bash
mdrap historical ingest --exchange binance_spot --symbol BTCUSDT \
  --start 2025-08-01T20:00:00Z --end 2025-08-01T20:01:00Z \
  --output data/chd-runs/btc-minute
```

### 4. DuckDB Columnar Time-Series Storage & SIMD Analytics (`src/columnar.py`, Spec §14, §26)
- **High-Throughput Embedded Columnar Store**: Embedded in-process DuckDB analytical engine with SIMD-vectorized execution for ultra-fast billion-tick historical queries.
- **Zero-Copy SQLite Sync**: Directly attaches operational SQLite databases via DuckDB's native SQLite scanner (`ATTACH '...' AS sqldb (TYPE SQLITE)`) and bulk copies 300,000+ ticks in ~2.8s into columnar storage.
- **Vectorized Resampling & Aggregation**:
  - Resamples trade ticks into OHLCV candles via `arg_min(price, exchange_timestamp)` and `arg_max(price, exchange_timestamp)` in a single pass without window functions or self-joins (**37.2x faster than SQLite**).
  - Computes exact institutional VWAP (`sum(P*Q) / sum(Q)`) and total notional across tens of thousands of trades in <10ms (**63.3x faster than SQLite**).
  - Vectorized bid-ask spread analytics and crossed-market anomaly tracking.
  - Sub-microsecond latency quantile extraction (`p50`, `p90`, `p95`, `p99`, `p99.9`) across millions of records via `quantile_cont()`.
  - Discrete price-rung volume profile distribution.
- **Apache Parquet Compressed Export**: Direct export of tick universes to compressed `.parquet` files with Zstandard (`zstd`), Snappy, or GZIP compression (299k ticks compressed to 1.52 MB).

### 5. Consolidated Level-2 Market Depth & Real-Time VWAP Slicing (`src/depth.py`)
- Real-time aggregation of multi-venue order books into a consolidated L2 depth ladder.
- Dynamic **VWAP Slippage Curve calculation**: computes estimated executed price, basis point slippage, and market impact across any requested order size.
- Real-time bid/ask liquidity imbalances and multi-venue depth visualization via `cli.py depth` and `cli.py vwap`.

### 6. Institutional Financial Model & 5-Tab Excel Exporter (`src/exporter.py`)
- Translates live ticks, order book depth, and quality metrics into institutional-grade Microsoft Excel (`.xlsx`) workbooks:
  - **Tab 1: Executive Summary & Microstructure KPIs**: Total volume, VWAP, spreads, crossed quote count, tick count.
  - **Tab 2: Consolidated Market Depth**: Multi-venue aggregated bid/ask ladders with depth visualization.
  - **Tab 3: VWAP Slippage Curve**: Execution slippage schedule across order tranches.
  - **Tab 4: Quality & Quarantine Audit**: Detailed record of rejected/quarantined events with exact failure reasons.
  - **Tab 5: OHLCV Candlesticks**: 5-second candle aggregates (Open, High, Low, Close, Volume, Trades).
- Automatic fallback to structured CSV report directories if `openpyxl` is not installed.
- One-command generation and instant launch: `mdrap export AAPL --open`.

### 7. In-Place Live Terminal Ticker & Candlestick Charts (`src/terminal_display.py`)
- **Zero-Scroll In-Place Display**: Updates live market quotes and candlestick charts in-place using ANSI cursor repositioning without cluttering terminal history.
- **High-Resolution Candlestick Visualization**: Renders 3-character columns (` █ `, ` │ `, ` ┼ `) with distinct body margins and box-drawing wicks.
- **Outlier-Resilient Percentile Scaling**: Visual bounds clamped to the 10th–90th price percentiles so extreme anomalies never crush normal candles into a flat line.
- **Aligned Volume Histogram**: Synchronized volume bars underneath each candle column.
- Dedicated modes: full live ticker dashboard (`mdrap live`), ticker-only mode (`mdrap live AAPL --ticker-only`), and standalone historical chart viewer (`mdrap chart AAPL`).

### 8. Synthetic Consolidated NBBO & Multi-Market Connectors (`src/live.py`, `src/bbo.py`)
- Ingests real-time prices across major global crypto exchanges (**Binance, Coinbase, Kraken, OKX, Bybit**) and global equities (**AAPL, MSFT, NVDA, TSLA, SPY, QQQ, GOLD**).
- Computes global tightest bid/ask spread, mid-price, and real-time venue attribution with crossed-market flags.

### 9. Live Watchdog & Automated Source Failover (`src/watchdog.py`)
- Real-time source reliability tracking with silence detection and degradation alerts.
- Automated failover circuit breaker: dynamically evicts silent or corrupt feeds from the consolidated book with hysteresis recovery.

### 10. Immutable Raw Event Archive & Deterministic Replay (`src/archive.py`)
- Date- and source-partitioned write-ahead JSONL log capturing every raw event before processing.
- Deterministic event replay engine allows historical backtesting and auditing through the complete pipeline.

### 11. Enterprise Security & Cryptographic Merkle Audit (`src/security.py`)
- **HMAC-SHA256 Feed Authentication**: Anti-spoofing signature verification with pre-shared feed secrets.
- **Role-Based Access Control (RBAC)**: Privilege boundaries across `VIEWER`, `OPERATOR`, and `ADMIN`.
- **Token Bucket Rate Limiting**: Shields pipeline against denial-of-service and quote flooding ($20,000\text{ eps}$).
- **Tamper-Evident Merkle Audit Log**: Cryptographically chained SHA-256 hash trail in SQLite with standalone verification via `mdrap audit --verify`.

### 12. Binary Shared Memory IPC Transport (`src/shm.py`, `src/protocol.py`)
- Zero-copy lock-free ring buffer for ultra-low latency IPC between the ingestion daemon and trading algorithms.

### 13. SEC EDGAR Alternative Data & Corporate Research Engine (`src/research.py`)
- **8-K Material Event Taxonomy**: Real-time extraction and plain-English decoding of material SEC 8-K trigger items (e.g., `Item 5.02` executive departures/elections, `Item 2.02` earnings announcements, `Item 1.01` entry into material agreements, `Item 8.01` other events) classified by urgency (`CRITICAL`, `HIGH`, `MEDIUM`, `INFO`).
- **Form 4 Insider Trading XML Parser**: Deep inspection of executive and director transactions, distinguishing open-market buys, sells, option exercises, and stock grants with price, share count, and post-transaction ownership.
- **Audited GAAP Facts Database**: Instant retrieval of audited 10-K/10-Q financial metrics (Revenues, Net Income, Operating Margin) directly from SEC XBRL frames.
- **Enterprise Security & Multi-Tier Caching**: Hardened against SSRF and XXE injection; zero developer path leaks via regex scrubbing; multi-tier caching (in-memory + atomic disk cache with 300s TTL) reducing repeated queries from ~400 ms to **< 1 ms**.

### 14. Institutional Quantitative Research, Trading & Risk Suite (Gaps 1–12)
- **Historical Backtesting Engine (`src/backtest.py`)**: Event-driven backtesting with Sharpe, Sortino, Calmar ratios, high-watermark drawdown curves, win rate, profit factor, and walk-forward out-of-sample optimization (`mdrap backtest`).
- **Portfolio Risk & Value-at-Risk Engine (`src/risk.py`)**: Historical simulation, Parametric, and Monte Carlo VaR, Expected Shortfall (CVaR), and multi-tier circuit breakers (`mdrap risk`).
- **Persistent Multi-Timeframe Bar Database (`src/bardb.py`)**: Incremental candle rollups across 7 intervals (`1s` to `1d`) with WAL SQLite storage and temporal as-of queries (`mdrap bars`).
- **Options & Derivatives Pricing Engine (`src/options.py`)**: Pure-Python Black-Scholes-Merton European & Cox-Ross-Rubinstein Binomial American models, full Greeks chain (Delta through Volga), Newton-Raphson IV solver, and volatility surface modeling (`mdrap options`).
- **News Aggregation & Financial Sentiment Pipeline (`src/news.py`)**: Real-time RSS/Atom feed parsing, cashtag extraction, and keyword-driven financial sentiment scoring with price impact correlation (`mdrap news`).
- **Real-Time Alert Engine (`src/alerts.py`)**: Configurable price, spread, volume, and feed silence alerts (`mdrap alert`).
- **Watchlists & Portfolio Tracker (`src/portfolio.py`)**: Multi-symbol watchlists and portfolio mark-to-market accounting (`mdrap watchlist`, `mdrap portfolio`).
- **Corporate Actions Engine (`src/corporate_actions.py`)**: Cumulative split, dividend, and ticker change adjustments (`mdrap corpact`).
- **ML Feature Store (`src/features.py`)**: Technical indicators (RSI, MACD, Bollinger, ATR) and microstructure features (VPIN, order imbalance) (`mdrap features`).
- **Automated Task Scheduler (`src/scheduler.py`)**: Cron-style interval scheduler with EOD rollups (`mdrap schedule`).
- **FIX Protocol Engine (`src/fix_engine.py`)**: FIX 4.2/4.4 message encoder, decoder, checksum validator, and session heartbeat manager.
- **Multi-Asset Class Data Model (`src/models.py`)**: First-class support for Equities, Crypto, Futures, Options, Bonds, and FX.
- See [**Research & Live Trading Guide**](docs/RESEARCH_AND_TRADING.md) for detailed tutorials and architecture.

### 15. Global Multi-Market Infrastructure & International Trading Desk (`src/venues.py`, `src/symbology.py`, `src/fx.py`)
- **ISO 10383 Venue Registry & Microstructure Engine**: Native support for Indian (`XNSE`, `XBOM`), German (`XETR`, `XEUR`), Japanese (`XTKS`, `XOSE`), UK (`XLON`), Hong Kong (`XHKG`), and US (`XNAS`, `XNYS`) exchanges.
- **Global Trading Desk Clock (`mdrap markets`)**: Real-time session state machine tracking active phase (`PRE_OPEN`, `CONTINUOUS`, `VOLATILITY_HALT`, `CLOSING_AUCTION`, `CLOSED`), time until next bell, benchmark index, and circuit breaker bands.
- **Universal Symbology Resolver**: Resolves exchange suffixes (`.NS`, `.BO`, `.DE`, `.T`), Japanese 4-digit codes (`7203`, `6758`), Reuters RICs (`RELI.NS`), Bloomberg tickers (`RELIANCE:IN`), and ISINs.
- **Institutional FX Matrix Engine & Multi-Currency Accounting**: Real-time triangular cross-currency conversion with localized formatting (Indian Lakh/Crore commas `₹1,25,000.00`, Japanese Yen integers `¥3,500`). Portfolio valuation in any target currency via `mdrap portfolio --currency <CODE>`.
- **Venue-Aware Anomaly Detection**: Prevents exchange-curbed events from corrupting data streams with microstructure-specific reason codes: `CIRCUIT_FILTER_BREACH` (NSE/BSE), `VOLATILITY_INTERRUPTION` (Xetra/Eurex), and `SPECIAL_QUOTE_INDICATION` (TSE/JPX).
- **Multi-Market Simulation Profiles**: Seed realistic international market sessions with `mdrap run --market {nse,xetra,tse,global}`.

### 16. Global Maritime Tanker & Cargo Tracking Alternative Data Engine (`src/vessel.py`)
- **Real-Time AIS Vessel Intelligence**: Tracks commercial crude oil tankers (VLCC/ULCC), LNG carriers, dry bulk carriers, and container ships with vessel payload, deadweight tonnage (DWT), and load status (`LADEN` vs `BALLAST`).
- **Geopolitical Maritime Chokepoints**: Monitors 8 strategic bottlenecks (Strait of Hormuz, Strait of Malacca, Suez Canal, Bab-el-Mandeb, Panama Canal, Bosphorus, Cape of Good Hope, Dover Strait) with daily flow volumes and proximity surveillance.
- **Physical-to-Financial Commodity Mapping**: Maps physical cargo flows (Arab Light, Brent Crude, LNG, Iron Ore) and chartering commodity majors (Saudi Aramco, Shell, BP, Vitol, Trafigura, Vale) directly to energy and commodity derivatives.
- **Native C Vectorized Geodesic Fastpath**: High-performance C geofencing evaluates 10,000 vessels across global chokepoints in **23.08 ms (~288 ns per chokepoint check)** using Axis-Aligned Bounding Box (AABB) spatial pre-filtering (`mdrap vessel`).

### 17. Modal Keyboard Navigator Desk (`src/navigator.py`, `mdrap desk`)
- **Vim-Inspired Modal Navigation**: High-velocity order desk with strict state machine separation between `NAVIGATING`, `FILTERING`, and `ORDER_ARMED` execution modes.
- **Directional Grid Traversal**: Vim keys (`h`/`j`/`k`/`l` or arrow keys) navigate active symbol watchlists, market stats, and Level-2 order books.
- **Quick Metric Sorting & Filtering**: One-key sorting (`s`) by Volume, Spread, Change %, or Symbol, and live incremental filtering (`/`) with instant debounce.
- **Two-Stage Armed Execution Safeguard**: Pressing `b` (Buy) or `S` (Sell) arms a dedicated order ticket with clear color warning; requires explicit confirmation (`Enter` or `y`) to execute or `Esc`/`n` to safely cancel, completely preventing stray key accidental order submissions.

### 18. Gate Audit, Configuration & Extension Points (Round 2 Architecture)
- **Single Source of Truth (`src/rules.def`)**: Canonical X-Macro reserving bitmask ranges: bits 0–15 (core quality rules), bits 16–31 (future platform rules), and bits 32–63 (user-defined rules). Python `Reason` enum and native C `#define`s are auto-synchronized and validated in CI via `python tools/gen_reasons.py --check`.
- **Hierarchical Zero-Dependency Configuration (`mdrap.toml`, `src/config_loader.py`)**: Powered by Python 3.11+ stdlib `tomllib` with layered resolution provenance tracking: `defaults -> venue -> instrument_class -> instrument`. Queryable on the CLI via `mdrap config show --venue binance --instrument BTCUSDT`.
- **Feed Adapter Protocol & User Rule Registry (`src/adapters/`, `src/rules.py`)**: `@runtime_checkable` `FeedAdapter` protocol (`open`, `__iter__`, `close`) with dynamic discovery via entry points (`mdrap.adapters`), and user rule registration decorator `@register_rule(bit=32..63, name=...)` evaluated in Python post-native pass.
- **Institutional Diagnosability (`mdrap doctor`, `mdrap demo`)**: Instant self-checks verifying Python environment, compiler detection on PATH, active engine tier, configuration SHA-256, SQLite WAL status, and 10k smoke benchmark execution; `mdrap demo` executes 50k events into the live desk view.
- **Reproducibility Manifests & Parquet Export (`src/manifest.py`, `src/export.py`)**: Generates structured `manifest.json` capturing git commit SHA, config hash, active engine tier, and system hardware. Exports canonical data and quarantine tables to Parquet (`--format parquet`), JSON, or CSV.
- **Continuous Fuzzing (`fuzz/`)**: libFuzzer C harnesses and differential fuzzer testing 5,000+ mutated byte streams to assert 100% acceptance/rejection parity between Python and C decoders.

### 19. Standalone Native Core & T1 Zero-Lock Ring Buffer (Round 3 Architecture)
- **Zero-Python Hot Path (`src/mdrap_core.c`)**: Standalone compiled binary (`mdrap-core` / `mdrap-core.exe`) executing wire-to-SHM directly without Python runtime, CPython FFI, or GIL involvement, achieving **22.35 Million events/sec** (**44.8 ns per tick** wire-to-SHM latency).
- **Zero-Lock SPSC Shared Memory**: Cross-platform memory-mapped circular ring buffer with 128-byte cache-line aligned slots, atomic release fences, and two-phase commit protocol (`UNCOMMITTED` seq invalidation $\to$ payload write $\to$ fence $\to$ commit sequence publication).
- **Hardware Timestamping Diagnostics (`mdrap doctor`)**: Institutional clock source diagnostics detecting `SO_TIMESTAMPING` capabilities and Linux PTP Hardware Clocks (`/dev/ptp*`), with graceful fallback to software QPC on Windows.
- **Lock-Free Contention Proof (`benchmarks/bench_contention.py`)**: Multi-source contention benchmark proving flat p99.9 tail latency (0.30 µs at 8 sources) and eliminating mutex convoying.
- **Architecture Decision Record**: [ADR 0003: Native Core Process Split](docs/decisions/0003-native-core-process-split.md).
- **T2 FPGA Learning Track (`fpga/`)**: Explores the boundary between software T1 and hardware T2:
  - **Verilog RTL Modules**: [`fpga/mdrap_crossed_quote.v`](fpga/mdrap_crossed_quote.v) (64-bit carry-chain comparator), [`fpga/mdrap_sequence_gap.v`](fpga/mdrap_sequence_gap.v) (pipelined gap and retrograde detector), and [`fpga/tb_mdrap_rules.v`](fpga/tb_mdrap_rules.v) (self-checking testbench).
  - **Cycle-Accurate Parity**: [`tests/test_fpga_parity.py`](tests/test_fpga_parity.py) verifies 100% agreement against Python and C engines across 1,000 synthetic events.
  - **Findings Report**: [FPGA Spike Findings](docs/fpga-spike-findings.md) detailing resource usage (~130 LUTs, 69 FFs, ~3.3 ns evaluation) and an honest assessment of the ~100 ns gap to commercial tick-to-trade appliances.

---

## Latency Architecture & Industry Tiering

| Tier | Industry Scope & Technology | Representative Latency | Reachable by MDRAP? |
|---|---|:---:|:---:|
| **T0 — Baseline** | In-process Python/C pipeline, SQLite WAL persistence | ~15 µs in-memory, ~780 µs durable | **Shipped (v2.1)** |
| **T1 — Good Software** | Standalone native core (`mdrap-core`), zero-lock SPSC shared memory ring, kernel-bypass (`SO_TIMESTAMPING`/`io_uring`/`AF_XDP`), core isolation | **~1–10 µs** wire-to-decision | **Target Architecture** (Phases 17–21) |
| **T2 — Specialist Hardware** | Commercial FPGA tick-to-trade appliances | Sub-microsecond (~100 ns) | **Bounded Learning Spike** (Phase 22, `fpga/`) |
| **T3 — Physical Infra** | Colocation, cross-connects, microwave/laser links | Sub-100 ns transport | **Out of Scope** (Real estate & capital budget) |

> **Platform Target Note**: Linux (kernel 5.10+, x86_64) is the production target platform for T1 execution (`SO_TIMESTAMPING`, `io_uring`, `AF_XDP`, `isolcpus`). Windows is supported for local development, control plane, and functional testing.
>
> 📖 **Deep Dive**: See [From T0 to T1: The MDRAP Low-Latency Architecture Journey](docs/T0_TO_T1_JOURNEY.md) for full empirical benchmarks, thread contention analysis, and systems engineering trade-offs.

---

## Architectural Progression & Benchmarks

Measured on identical 100,000-event workloads (`seed=42`, 7 timed runs, 2 warmup) via `python benchmarks/compare_v1_v4.py --events 100000 --seed 42`:

| Architecture | Throughput (eps) | Proc Latency p50 | Latency IQR | Design Highlight |
|---|:---:|:---:|:---:|---|
| **V1 Synchronous Baseline** | **28,562 eps** | **21.0 µs** (21,000 ns) | 0.40 µs | Pure Python, synchronous loop, SQLite batched writes |
| **V2 Decoupled Streaming** | **22,351 eps** | **15.3 µs** (15,300 ns) | — | Multi-threaded in-memory queue bus with backpressure |
| **V4 Native C Hot Path** | **34,241 eps** | **15.3 µs** (15,300 ns) | 0.35 µs | GCC `-O3` ctypes binding with zero-lookup ID interning |
| **Native C Direct Batch** | **18,669,082 eps** | **50.0 ns** (0.050 µs) | — | Contiguous C arrays in CPU L1 cache |
| **V5 Standalone Core (T1)** | **22,345,370 eps** | **44.8 ns** (0.045 µs) | 0.10 µs | Out-of-process C binary, zero-lock SPSC shared memory |

### Latency Hierarchy & Physical Bounds

- **Native C Batch (50.0 ns) vs Pipeline (15.7 µs)**: The **50.0 ns** figure measures the C accelerator alone operating on pre-batched contiguous arrays in CPU L1 cache. The **15.7 µs** figure is the same accelerator measured end-to-end inside the full pipeline (gateway → quality engine → reconciliation → storage).
- **Physics of the "~5 Nanosecond" Myth**: At 4.0 GHz, one CPU cycle is 0.25 nanoseconds; **5 nanoseconds is exactly 20 CPU cycles**. Software running on general-purpose operating systems cannot receive network packets, parse payloads, and evaluate state in 5 nanoseconds (PCIe bus transfer from NIC to RAM alone takes 100–250 ns). Sub-20ns latencies are only physically possible in dedicated **hardware FPGA gate logic** (e.g. AMD Xilinx UltraScale+).

#### The 3 Measurable Platform Latency Tiers
| Tier | Scope / Boundary | Latency (p50) | Throughput | Use Case |
|---|---|:---:|:---:|---|
| **Tier 1: Core C L1 Algorithm** | Isolated Native C rolling math (`fastpath.c`) | **50.0 ns** | 18,669,082 eps | Micro-benchmark core arithmetic |
| **Tier 2: In-Memory Pipeline** | End-to-end compute path: gateway + 7 quality rules + BBO | **14.90 µs** (V4) / **19.00 µs** (V1) | 33,686 eps (V4) / 29,624 eps (V1) | Real-time IPC streaming to bots |
| **Tier 3: Durable Ingest-to-Disk** | Full pipeline with SQLite WAL batched disk persistence | **783.6 µs** | 18,000–22,000 eps | Regulatory audit & persistent storage |

> **Reproducibility Command**: Run `python benchmarks/compare_v1_v4.py --events 50000 --seed 42` to reproduce V1 vs V4 benchmarks back-to-back across 7 timed runs. See [`docs/benchmark-methodology.md`](docs/benchmark-methodology.md) for full denominator definitions.

---

## Quick Start

### Installation

**1. Install from PyPI (Recommended)**
MDRAP is officially published on PyPI and can be installed with zero external setup:
```bash
pip install mdrap
```

> **Note for Microsoft Store Python users on Windows:** If you installed Python via the Microsoft Store, `pip` might install the `mdrap` script into a folder that isn't automatically added to your system's `PATH`. If the `mdrap` command is not recognized, you can always run the platform using:
> ```bash
> python -m cli
> ```

**2. Clone from Source (For Development)**
Clone the repository to get the latest source and install optional visualization and test dependencies:
```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap
pip install -r requirements.txt
```

Compile the Native C accelerator (optional — transparent pure-Python fallback is included):
```bash
python build_fastpath.py
```

---

### 1. Interactive Wall Street & Quant Terminal
Run `mdrap` (or `.\mdrap.bat`) with zero arguments to enter the pre-warmed shell:
```bash
.\mdrap.bat
```
```text
mdrap> LIVE AAPL         # Live market stream with in-place updating table & candlestick chart
mdrap> DESK              # Launch modal keyboard navigator desk with Vim controls & armed tickets
mdrap> CHART BTC/USD     # Standalone visual candlestick chart with volume histogram
mdrap> DEPTH AAPL        # Consolidated Level-2 market depth ladder
mdrap> VWAP AAPL 1000    # Calculate VWAP slippage curve for 1,000 shares
mdrap> EXPORT AAPL --open# Export 5-tab financial model workbook to Excel and open it
mdrap> BTC BBO           # 5-Venue Consolidated NBBO across Binance, Coinbase, Kraken, OKX, Bybit
mdrap> TOP               # Launch real-time full-screen service cockpit
mdrap> STRESS            # Run multi-directional stress tests and 1M-1B scale analysis
mdrap> AUDIT             # Cryptographically verify tamper-evident Merkle hash chain
mdrap> ?                 # Open clean 4-quadrant command palette
```

You can also run all commands directly from PowerShell / CMD / Bash:
```bash
.\mdrap.bat desk                      # Launch high-velocity modal keyboard trading desk
.\mdrap.bat live AAPL                 # In-place terminal ticker & candlestick chart
.\mdrap.bat live AAPL --ticker-only  # Clean single-table ticker view
.\mdrap.bat chart AAPL                # Unicode candlestick chart
.\mdrap.bat depth AAPL                # L2 market depth ladder
.\mdrap.bat vwap AAPL --size 500      # Execution slippage schedule
.\mdrap.bat export AAPL --open        # Generate Excel model (.xlsx) & open immediately
.\mdrap.bat bbo BTC/USD               # 5-Venue crypto NBBO quote
.\mdrap.bat top                       # Terminal service cockpit
.\mdrap.bat stress --module quality   # Benchmark C hotpath (18.6M eps)
```

---

### 2. Headless Daemon & Live IPC Streaming
In **Terminal 1**, start the background ingestion daemon:
```bash
# High-speed simulated multi-venue feed
.\mdrap.bat daemon --speed 2000

# Or live multi-venue market feeds (Binance, Coinbase, Kraken, OKX, Bybit)
.\mdrap.bat daemon --live
```

In **Terminal 2**, stream clean canonical ticks directly to stdout or pipe into trading algorithms:
```bash
# Formatted ANSI color stream
.\mdrap.bat sub BTC/USD

# Raw JSON stream for automated algorithmic bots or jq
.\mdrap.bat sub BTC/USD --json | jq '{bid: .bbo.bid, ask: .bbo.ask}'
```

In **Terminal 3**, launch the real-time terminal monitor:
```bash
.\mdrap.bat top
```

---

## Keyboard-First Speed Ergonomics

MDRAP provides sub-second keyboard ergonomics inspired by Bloomberg terminals (`<TICKER> <FUNCTION> <GO>`), eliminating long CLI commands for high-speed trading desks and quant operations:

### 1. Wall Street 2-Token Mnemonic Shell
Inside the interactive shell (`.\mdrap.bat` or `./mdrap`), type:
- `AAPL C` -> Candlestick Chart HUD
- `BTC D` -> Consolidated Level-2 Depth Book Ladder
- `AAPL V` -> Institutional Real-Time VWAP Slippage Curve
- `AAPL P` -> Polygon.io Streaming WebSocket Feed
- `ES B` -> Databento Binary DBN Fast Streaming Feed
- `AAPL X` -> 5-Tab Financial Model Excel Export (Auto-Opens)
- `AAPL` (ticker only) -> Instant Consolidated NBBO Quote
- `1` to `9` -> Instant 1-Key Launches (`1` = Live BTC, `2` = NBBO, `3` = Cockpit, `4` = Chart, etc.)

### 2. Live In-Stream Hotkeys (Non-Blocking Keystrokes)
During any live stream (`mdrap live`, `mdrap top`, `mdrap depth`), hands never leave the keyboard:
- `[Space]` -> **Freeze / Unfreeze Frame**: Pauses the live rendering so you can inspect fast-moving prints and L2 depth levels without them scrolling away. Pressing `[Space]` again resumes real-time updates.
- `[q]` or `[Esc]` -> **Instant Clean Exit**: Cleanly restores terminal cursor without Python stack traces.
- `[c]` -> **Toggle Candlestick HUD**: Show or hide the inline technical chart.
- `[d]` -> **Toggle Level-2 Depth Ladder**: Show or hide the consolidated depth rungs.
- `[Tab]` / `[1-9]` -> **Switch Active Symbol Focus**: Cycle or jump between active universe tickers on the fly.

### 3. Single-Letter OS CLI Shortcuts
From your terminal (PowerShell, CMD, or bash):
```bash
mdrap c AAPL    # Candlestick Chart
mdrap d BTC     # Level-2 Depth Ladder
mdrap v AAPL    # Real-Time VWAP Curve
mdrap p AAPL    # Polygon.io Streaming Feed
mdrap b ES      # Databento DBN Streaming Feed
mdrap x AAPL    # 5-Tab Excel Export
mdrap AAPL      # Instant Best Bid & Offer Quote
mdrap edgar AAPL# SEC 8-K Material Corporate Events & Form 4 Insider Trades
mdrap company AAPL # SEC Corporate Profile & Audited Financials
```

### 4. Modal Trading Navigator Desk (`mdrap desk`)
For rapid trading desk operations with zero mouse latency and full keyboard control:
- `[h]` / `[j]` / `[k]` / `[l]` -> Vim-style directional navigation across symbols and depth books.
- `[/]` -> Instant incremental symbol search and filtering with live debounce.
- `[s]` -> Cycle sort metrics (Volume, Spread, Change %, Symbol).
- `[1-5]` -> Instant watchlist switching (US Tech, Crypto, Futures, FX, All).
- `[b]` / `[S]` -> Two-stage armed execution tickets (Buy / Sell) with confirmation protection (`[Enter]`/`[y]` to submit, `[Esc]`/`[n]` to abort).

---

## CLI Command Reference

| Command | Aliases | Description |
|---|---|---|
| `status` | `s`, `stat` | Show comprehensive platform status overview, database statistics, and engine readiness |
| `desk` | `terminal`, `nav` | Launch high-velocity modal keyboard navigator desk with Vim controls and armed order tickets |
| `shell` | `sh` | Launch low-latency interactive slash-command terminal shell |
| `live` | `stream`, `watch`, `ticker` | Stream live market ticks with in-place updating table & candlestick chart (`--feed polygon/databento`) |
| `feed` | `stream-feed`, `feeds` | Inspect, benchmark, and test direct streaming feeds (Polygon, Databento, Crypto WS) |
| `chart` | `candle`, `graph` | Display visual in-terminal ASCII/Unicode candlestick chart with volume histogram |
| `depth` | `l2`, `book`, `ladder` | Display Consolidated Level-2 Multi-Venue Market Depth Ladder |
| `vwap` | `curve`, `slip` | Compute multi-venue real-time VWAP execution & slippage curves |
| `export` | `exp`, `excel`, `xlsx` | Export market microstructure data to 5-tab Excel (.xlsx) or CSV package |
| `bbo` | `nbbo` | Query Synthetic Consolidated Best Bid & Offer (NBBO) across 5 exchanges |
| `edgar` | `research`, `events`, `filings`, `company`, `insiders` | SEC EDGAR Alternative Data: 8-K material events, Form 4 insider trades, XBRL GAAP facts |
| `vessel` | `vessels`, `tankers`, `ships`, `ais`, `cargo` | Maritime Tanker & Cargo Tracking: Crude oil, LNG, bulk, container tracking & chokepoints |
| `run` | `r` | Run the validation pipeline against the simulator (with live HUD, `--strict-sync`, `--no-sync`) |
| `benchmark` | `bench`, `b` | Run controlled benchmark and score quality detection against ground truth |
| `compare` | `comp`, `c` | Run V1 Baseline and Native C Hot Path on identical workloads and print comparative report |
| `loadtest` | `load`, `l` | Sweep increasing event volumes (10k to 250k) and report performance trend |
| `stress` | `str` | Run multi-directional stress testing suite and 1M–1B transaction scale analysis |
| `chaos` | `ch` | Execute automated chaos & resilience drills (source kill, network jitter, storage outage) |
| `watchdog` | `w`, `wd` | Show source health status, silence alerts, and automated failover events |
| `security` | `sec` | Display platform security posture, HMAC verification, RBAC, and rate limiting status |
| `keys` | — | Manage client API keys and entitlements |
| `audit` | — | View and cryptographically verify signed hash audit logs with external anchors |
| `query` | `q` | Inspect stored SQLite tables: health, latest ticks, lineage trail, and quarantine |
| `historical` | `history`, `chd` | Discover, download and ingest CHD history with verified files and replay provenance |
| `replay` | `rep` | Replay archived raw events deterministically through the pipeline |
| `archive` | `arc` | Show immutable raw event JSONL archive statistics |
| `analytics` | `a`, `an` | Query 5s OHLCV candles, bid-ask spreads, and realized volatility |
| `columnar` | `col`, `duck`, `duckdb` | Query DuckDB columnar storage, vectorized SIMD OHLCV/VWAP, zero-copy SQLite sync, and Parquet export |
| `daemon` | `d` | Run headless streaming socket daemon service (Spec §18) |
| `sub` | `subscribe`, `listen` | Subscribe to daemon stream and output formatted ticks or depth to stdout |
| `top` | `mon`, `monitor` | Launch dynamic full-screen terminal service cockpit |
| `test-all` | `test`, `t` | Run all platform CLI commands, benchmarks, queries, and verifications in one pass |
| `throughput`| `tp`, `meps` | Benchmark vectorized Native C SBE stream (500k-1M+ eps target) with `--compare` |
| `tca`       | `bestex`    | Run institutional Best Execution & Transaction Cost Analysis (SEC 606) |
| `flow`      | `cvd`       | Track institutional order flow, Lee-Ready aggressor side, and Cumulative Volume Delta |

---

## Installation & Quickstart

MDRAP provides **zero-configuration native C acceleration** whether acquired via Git or Pip.

### 1. From Git (Automated JIT Native Compilation)
```bash
# Clone the repository
git clone https://github.com/aryan-20-04/mdrap.git
cd mdrap

# Run immediately (JIT auto-compiles fastpath on first import)
python cli.py status

# Optional: manually build or test native C shared library
python build_fastpath.py
```

### 2. From Pip
```bash
# Standard local install
pip install .

# Editable development install
pip install -e .

# Direct from GitHub repository
pip install git+https://github.com/aryan-20-04/mdrap.git
```

> [!NOTE]
> When a C compiler (GCC, Clang, MSVC) is present, the native shared library compiles automatically. If no compiler exists, MDRAP seamlessly runs with 100% numerical parity on pure Python standard library fallbacks.

---

## Real-World Live Commands (Interactive Testing)

MDRAP includes **intelligent fuzzy typo auto-correction** (e.g. `edgar fillings NVDA` auto-resolves to `filings`, `mdrap choas` auto-resolves to `chaos`) and scoped error formatting:

### 1. SEC EDGAR Fundamental & Corporate Research
```bash
# View last 5 official SEC filings for NVDA (Form 4, 8-K, 10-Q)
mdrap edgar filings NVDA -l 5

# Inspect insider executive transactions (Form 4 purchases, sales, option awards)
mdrap edgar insiders NVDA -l 10

# Material 8-K trigger events (earnings announcements, executive changes, M&A)
mdrap edgar events AAPL

# Audited GAAP financial facts from SEC XBRL frames
mdrap edgar facts MSFT
```

### 2. Live Market Microstructure & Order Books
```bash
# Consolidated Top-of-Book (NBBO) across 5 global venues
mdrap bbo BTC/USD
mdrap bbo AAPL

# Consolidated Level 2 depth ladder
mdrap depth AAPL

# Institutional VWAP slippage curve & execution impact
mdrap vwap NVDA

# Live terminal streaming ticker (Ctrl+C to exit)
mdrap live BTC/USD
```

### 3. Quantitative Analytics & Technical Charting
```bash
# Visual ASCII / Unicode candlestick chart with volume histogram
mdrap chart AAPL

# Multi-timeframe OHLCV bar rollup
mdrap ohlcv AAPL -i 1m -l 10

# Cross-exchange spread & divergence analytics
mdrap spread AAPL
```

### 4. Whale Order Flow & Institutional TCA
```bash
# Real-time order flow, Lee-Ready aggressor classification, and CVD tracker
mdrap flow NVDA

# Institutional Post-Trade Best Execution & Slippage Audit (SEC 606)
mdrap tca AAPL
```

### 5. Quantitative Derivatives & Options Pricing
```bash
# European options pricing with full Greeks (Delta through Volga)
mdrap options price -u NVDA -s 120 -k 120 -e 30

# Full options chain generation with Max Pain strike calculation
mdrap options chain -u AAPL -s 150
```

### 6. Chaos Resilience Drills & High-Throughput Benchmarks
```bash
# Execute all automated chaos drills (feed kill, jitter, storage failover)
mdrap chaos all

# Benchmark compiled C fastpath streaming throughput (100,000 events)
mdrap throughput -e 100000

# Full platform empirical latency benchmark
mdrap benchmark -e 50000
```

### 7. Alternative Data: Global Maritime Intelligence
```bash
# Live AIS commercial tanker and cargo vessel tracking
mdrap vessel list -l 10

# Geopolitical maritime chokepoints status (Hormuz, Malacca, Suez, Panama)
mdrap vessel chokepoints
```

### 8. Interactive Quant Trading Shell
Launch the resident in-memory REPL for sub-millisecond execution:
```bash
mdrap
```
```text
mdrap> edgar filings NVDA -l 5
mdrap> chart AAPL
mdrap> flow NVDA
mdrap> options price -u NVDA -s 120 -k 120 -e 30
mdrap> status
```

---

## Verification & Testing

MDRAP includes an institutional test suite of **650 automated unit, integration, quantitative, options, native C fastpath, and resilience tests** (100% passing):

### 1. With FastPath (Default Production Mode)
```bash
python -m pytest tests/ -q
# Result: 650 passed in ~85s (0 failed, 0 skipped)
```

### 2. Without FastPath (Pure Python Fallback Mode)
Simulate an environment without native C shared libraries by setting `MDRAP_DISABLE_FASTPATH=1`:
```bash
# PowerShell:
$env:MDRAP_DISABLE_FASTPATH="1"; python -m pytest tests/ -q; Remove-Item Env:\MDRAP_DISABLE_FASTPATH

# Bash / Linux / macOS:
MDRAP_DISABLE_FASTPATH=1 python -m pytest tests/ -q
# Result: 597 passed, 7 skipped in ~70s
```

### 3. Verification Scorecard & Architectural Comparison
```bash
# Platform verification scorecard
.\mdrap.bat test-all

# Empirical V1 (Pure Python), V2 (Streaming), V4 (Native C) benchmark
python cli.py compare

# SBE 50M+ EPS hardware saturation benchmark
python cli.py throughput -e 1000000 --compare
```

---

## Repository Structure

```text
mdrap/
├── cli.py               # Unified CLI, interactive quant shell, and command dispatcher
├── mdrap.bat            # Windows zero-config launcher script
├── mdrap                # Unix bash launcher script
├── build_fastpath.py    # Multi-compiler build script (GCC / Clang / MSVC)
├── config.yaml          # Externalized quality thresholds, anomaly windows & security policies
├── pyproject.toml       # PEP 518/621 packaging & 70 module distribution spec
├── setup.py             # Automated C fastpath compilation hooks on pip install
├── MANIFEST.in          # Source and binary wheel manifest
├── requirements.txt     # Optional runtime & dev dependencies (pure stdlib default)
├── LICENSE              # MIT License
├── .gitignore           # Production-grade gitignore for Python, C artifacts, and data
│
├── src/                 # Core MDRAP Platform Engine (70 Modules)
│   ├── alerts.py        # Persistent price, spread, volume spike, and drawdown alert engine
│   ├── analytics.py     # 5s OHLCV candles, bid-ask spread tracking, Welford realized volatility
│   ├── archive.py       # Immutable write-ahead JSONL archive & deterministic replay
│   ├── backtest.py      # Historical backtesting engine & walk-forward optimization
│   ├── bardb.py         # Persistent multi-timeframe OHLCV bar database (SQLite WAL)
│   ├── bbo.py           # Synthetic Consolidated BBO (NBBO) multi-venue engine
│   ├── benchmark.py     # Micro-benchmark harness & ground-truth scoring
│   ├── broker.py        # Thread-safe in-memory streaming bus with backpressure
│   ├── chaos.py         # Automated failure injection & chaos drill suite (Spec §15)
│   ├── chd.py           # CryptoHFTData historical downloader, parquet parser & CLI
│   ├── client.py        # Unified streaming client SDK & async TCP gateway client
│   ├── columnar.py      # DuckDB columnar engine, zero-copy SQLite scanner & Parquet exporter
│   ├── config.py        # Central configuration manager & asset-class override resolver
│   ├── corporate_actions.py # Splits, dividends, symbol changes & adjusted price series
│   ├── dashboard.py     # Real-time terminal pipeline telemetry HUD
│   ├── databento_feed.py# Databento DBN binary decoding (MBP-1, MBP-10, Trades) & streaming
│   ├── depth.py         # Consolidated L2 depth aggregation & VWAP slippage curve engine
│   ├── exporter.py      # Institutional 5-tab Excel (.xlsx) & CSV financial model exporter
│   ├── fastpath.c       # Native C hot path accelerator (GCC -O3 / Clang / MSVC)
│   ├── fastpath.dll     # Pre-compiled high-performance native C shared library
│   ├── fastpath.py      # C ctypes wrapper with JIT auto-compilation & Python fallback
│   ├── features.py      # Feature store: RSI, Bollinger Bands, ATR, VWAP, micro-imbalance
│   ├── feed_handler.py  # Unified streaming supervisor (Polygon, Databento, Crypto WebSockets)
│   ├── fix_engine.py    # FIX 4.2 / 4.4 protocol parser, serializer & session manager
│   ├── flow_tracker.py  # Institutional order flow, Lee-Ready aggressor & CVD tracker
│   ├── gateway.py       # Ingestion gateway, timestamp recorder, and schema normalizer
│   ├── live.py          # Multi-exchange connectors (Binance, Coinbase, Kraken, OKX, Bybit, Equities)
│   ├── metrics.py       # High-resolution hardware nanosecond latency & percentile telemetry
│   ├── models.py        # Multi-asset canonical event model (Equities, Crypto, Futures, Options)
│   ├── navigator.py     # High-velocity modal keyboard trading desk & armed order tickets
│   ├── news.py          # Financial news feed, headline sentiment & ticker extraction
│   ├── options.py       # Black-Scholes-Merton European, CRR Binomial American, Greeks & IV
│   ├── pipeline.py      # V1 synchronous baseline pipeline (ground-truth reference)
│   ├── polygon_feed.py  # Polygon.io streaming WebSocket connector (Quotes, Trades, Bars)
│   ├── portfolio.py     # Multi-asset portfolio manager, positions & lot accounting
│   ├── protocol.py      # Binary serialization & framing protocol for IPC
│   ├── quality.py       # 7-rule data quality evaluation engine (Spec §7)
│   ├── reconciliation.py# Multi-feed cross-reconciliation & dynamic reliability scoring
│   ├── research.py      # SEC EDGAR alternative data, Form 8-K taxonomy, Form 4 XML parser
│   ├── risk.py          # Institutional portfolio risk: VaR (3 methods), CVaR, Circuit Breakers
│   ├── scheduler.py     # Automated cron task scheduler (@hourly, @daily, @eod)
│   ├── security.py      # HMAC-SHA256 signing (private feeds), RBAC, Token Bucket rate limiter, signed audit log
│   ├── service.py       # Headless streaming daemon, authenticated socket, and service cockpit
│   ├── shm.py           # SHM v3 seqlock binary shared memory ring buffer IPC with epoch tracking
│   ├── simulator.py     # Deterministic feed simulator with seeded anomaly injections
│   ├── storage.py       # Batched SQLite store (canonical, quarantine, lineage, audit, health)
│   ├── strategy_sdk.py  # Algorithmic trading SDK, order books & paper execution sandbox
│   ├── stresstest.py    # Multi-directional stress benchmarks & 1B-scale profiling
│   ├── tca.py           # Institutional Best Execution & TCA Slippage Engine (SEC 606)
│   ├── term.py          # Auto-responsive terminal styling with stdlib fallback
│   ├── terminal_display.py # In-place live terminal ticker, dashboard HUD & ANSI charts
│   ├── trading_cli.py   # Quantitative trading CLI commands (backtest, risk, options, features)
│   ├── vessel.py        # Maritime vessel intelligence, commercial owner tags, geofencing
│   ├── watchdog.py      # Live source watchdog, silence detection & automated failover
│   └── ws_feed.py       # Async WebSocket live market feed connector
│
├── tests/               # 650 Automated Unit & Integration Tests (100% Passing)
│   ├── test_alerts.py
│   ├── test_analytics.py
│   ├── test_archive.py
│   ├── test_audit_hardening.py
│   ├── test_backtest.py
│   ├── test_bardb.py
│   ├── test_bbo.py
│   ├── test_chaos.py
│   ├── test_chd.py
│   ├── test_cli.py
│   ├── test_client.py
│   ├── test_columnar.py
│   ├── test_concurrent_users.py
│   ├── test_config.py
│   ├── test_corporate_actions.py
│   ├── test_databento_feed.py
│   ├── test_depth.py
│   ├── test_entitlements.py
│   ├── test_error_surfacing.py
│   ├── test_export.py
│   ├── test_fastpath.py
│   ├── test_fastpath_quantitative.py
│   ├── test_fastpath_throughput.py
│   ├── test_features.py
│   ├── test_feed_handler.py
│   ├── test_fix.py
│   ├── test_flow_tracker.py
│   ├── test_fx.py
│   ├── test_hardening.py
│   ├── test_itch.py
│   ├── test_keyboard_shortcuts.py
│   ├── test_live.py
│   ├── test_mbo.py
│   ├── test_multi_asset.py
│   ├── test_multicast_arbitrator.py
│   ├── test_multimarket_quality.py
│   ├── test_navigator.py
│   ├── test_news.py
│   ├── test_options.py
│   ├── test_pipeline_integration.py
│   ├── test_polygon_feed.py
│   ├── test_portfolio.py
│   ├── test_protocol.py
│   ├── test_quality.py
│   ├── test_research.py
│   ├── test_research_security.py
│   ├── test_risk.py
│   ├── test_sbe.py
│   ├── test_scheduler.py
│   ├── test_sdk.py
│   ├── test_security.py
│   ├── test_service.py
│   ├── test_service_resilience.py
│   ├── test_shm.py
│   ├── test_shm_decoupled.py
│   ├── test_strategy_sdk.py
│   ├── test_stresstest.py
│   ├── test_symbology.py
│   ├── test_system_limitations.py
│   ├── test_system_stress_and_adversarial.py
│   ├── test_tca.py
│   ├── test_terminal_display.py
│   ├── test_venues.py
│   ├── test_vessel.py
│   ├── test_vessel_fastpath.py
│   ├── test_vessel_stress.py
│   ├── test_vwap.py
│   ├── test_watchdog.py
│   └── test_ws_feed.py
│
├── docs/                # Architecture & Platform Specifications
│   ├── USER_GUIDE.md         # Comprehensive Operator & User Manual (all commands, flags, workflows)
│   ├── architecture.md       # Full platform architecture specification (V1–V4)
│   ├── audit-log-format.md   # Merkle tree hash chain format & audit specification (§19)
│   ├── benchmark-methodology.md # Scientific measurement standards & latency hierarchy
│   ├── data-model.md         # Canonical event schema & lineage data model
│   └── quality-rules.md      # 7-rule data quality evaluation definitions & fault scoring
│
└── benchmarks/          # Immutable benchmark runs, JSON reports, and cProfile traces
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

### Market Data Licensing & Redistribution Disclaimer

MDRAP is open-source financial-market infrastructure software designed to process, reconcile, and validate market data feeds that the user is legally authorized and licensed to receive. MDRAP does not provide, resell, or grant rights to redistribute proprietary exchange or vendor data (including CME, Nasdaq, NYSE, OPRA, Polygon.io, or Databento). Users are solely responsible for ensuring their ingestion, storage, processing, and downstream routing comply with their respective data vendor and exchange subscriber agreements.
