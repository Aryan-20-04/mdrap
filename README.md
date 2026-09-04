# Market Data Reliability & Acceleration Platform (MDRAP)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-200%2F200%20passing-brightgreen.svg)](tests/)
[![Hot Path Latency](https://img.shields.io/badge/hot--path-50.0%20ns%20%7C%2018.6M%20eps-orange.svg)](src/fastpath.c)
[![Architecture](https://img.shields.io/badge/architecture-V1%20%7C%20V2%20%7C%20V3%20%7C%20V4%20C--Fastpath-purple.svg)](docs/architecture.md)
[![Dependencies](https://img.shields.io/badge/dependencies-zero%20mandatory-success.svg)](requirements.txt)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A high-performance financial market infrastructure platform designed to ingest, validate, accelerate, and reconcile noisy, delayed, duplicated, and inconsistent market data from disparate exchanges and internal feeds into a unified, ultra-low-latency canonical stream with mathematical reliability scoring, cryptographic lineage auditing, and institutional execution analytics.

---

## High-Level Architecture

```mermaid
flowchart TD
    subgraph INGESTION ["1. Market Ingestion & Feeds"]
        B[Binance Public WebSocket/REST]
        C[Coinbase Public REST]
        K[Kraken / OKX / Bybit Public Feeds]
        EQ[Global Equities: AAPL, MSFT, NVDA, SPY]
        SIM[Deterministic Feed Simulator<br/>Seeded Faults & Anomaly Injections]
        WS[WebSocket Streaming Ingestor<br/>asyncio / websockets]
    end

    subgraph SECURITY ["2. Security & Gatekeeper (Spec §19)"]
        RL[Token Bucket Rate Limiter<br/>20,000 eps per IP/Key]
        SAN[Regex & Range Payload Sanitizer]
        HMAC[HMAC-SHA256 Signature Verification<br/>Constant-Time Digest]
        RBAC[RBAC Entitlement Guard<br/>VIEWER / OPERATOR / ADMIN]
    end

    subgraph PIPELINE ["3. Validation, Acceleration & Consensus Pipeline"]
        GW[Gateway & Normalization<br/>RawEvent -> CanonicalEvent]
        QE[7-Rule Quality Engine<br/>Schema, Dedup, Gap, Order, Stale, Crossed, 3-Sigma]
        FP[Native C Hot Path Accelerator<br/>8,192 Symbols | 18.6M eps | 50.0 ns]
        WD[Source Watchdog & Failover Circuit Breaker<br/>Silence & Degradation Monitoring]
        BBO[Synthetic Consolidated BBO<br/>5-Venue Multi-Exchange NBBO]
        DEPTH[Consolidated L2 Order Book<br/>Multi-Venue Depth Aggregation & VWAP Curves]
    end

    subgraph STORAGE ["4. Batched Storage, Archive & Audit (Spec §14, §19)"]
        CAN[(canonical_events<br/>WAL SQLite Batch)]
        QUAR[(quarantine<br/>Never Silently Drop)]
        LIN[(lineage<br/>Transformation Lineage Proof)]
        AUD[(audit_log<br/>Merkle Hash Chained)]
        ARC[[Immutable Raw JSONL Archive<br/>Write-Ahead Partitioned Log]]
        ANL[Analytical Storage Engine<br/>5s OHLCV Candles, Spreads, Volatility]
    end

    subgraph PRESENTATION ["5. Presentation, IPC & Institutional Export"]
        DAEMON[Headless Streaming Daemon<br/>Non-blocking Socket IPC]
        SHM[Binary Shared Memory Transport<br/>Zero-Copy Ring Buffer]
        LIVE[In-Place Live Terminal Ticker<br/>Cursor-Repositioned Rich HUD]
        CHART[Visual Candlestick Terminal Chart<br/>Unicode Wicks & Outlier Percentile Scaling]
        EXCEL[Institutional 5-Tab Excel Exporter<br/>XLSX Financial Model & CSV Packages]
    end

    B & C & K & EQ & SIM & WS --> RL
    RL --> SAN --> HMAC --> RBAC --> GW
    GW --> ARC
    GW --> QE
    QE <--> FP
    QE --> BBO & DEPTH
    QE --> WD
    BBO & DEPTH --> DAEMON & SHM
    QE --> CAN & QUAR & LIN & AUD & ANL
    BBO & DEPTH & ANL --> LIVE & CHART & EXCEL
```

---

## Key Platform Capabilities

### 1. 7-Rule Data Quality Engine (Spec §7)
- **Structural Schema Validation**: Rejects malformed JSON and missing sequence/timestamp attributes.
- **Sliding-Window Deduplication**: Identifies exact and sliding-window duplicate packet bursts without memory bloat.
- **Monotonic Sequence Gap Detection**: Detects missing exchange packets and penalizes feed reputation.
- **Out-of-Order Sequencing**: Catches retrograde arrival events across jittery network paths.
- **Timestamp Staleness Evaluation**: Flags lagging feeds exceeding max latency thresholds.
- **Crossed Quote Detection**: Flags invalid book states where $\text{Bid} > \text{Ask}$.
- **Statistical Price Sanity Checks**: Evaluates sudden price jumps ($>3\sigma$) using Welford's online variance algorithm.
- **Strict Quality Priority**: Non-downgradable status progression: `INVALID` > `SUSPICIOUS` > `VALID`. Quarantines bad data; **never silently drops events**.

### 2. Native C Hot-Path Accelerator (`fastpath.c`)
- Pure C implementation compiled with GCC `-O3` into a native shared library (`fastpath.dll`).
- **Capacity Expanded to 8,192 Symbols ($2^{13}$)** and 32 feed sources with dynamically allocated, SIMD-aligned contiguous memory arrays.
- **Zero-Division Bitshift Slot Indexing**: Computes slot offsets in 1 CPU cycle: `(source_id << 13) | instrument_id`.
- **Ultra-High Throughput**: Evaluates **18,669,082 events/sec (50.0 nanoseconds/event)** in batch mode.
- Seamless, transparent boundary fallback to pure Python if instrument universe exceeds 8,192 symbols.

### 3. Consolidated Level-2 Market Depth & Real-Time VWAP Slicing (`src/depth.py`)
- Real-time aggregation of multi-venue order books into a consolidated L2 depth ladder.
- Dynamic **VWAP Slippage Curve calculation**: computes estimated executed price, basis point slippage, and market impact across any requested order size.
- Real-time bid/ask liquidity imbalances and multi-venue depth visualization via `cli.py depth` and `cli.py vwap`.

### 4. Institutional Financial Model & 5-Tab Excel Exporter (`src/exporter.py`)
- Translates live ticks, order book depth, and quality metrics into institutional-grade Microsoft Excel (`.xlsx`) workbooks:
  - **Tab 1: Executive Summary & Microstructure KPIs**: Total volume, VWAP, spreads, crossed quote count, tick count.
  - **Tab 2: Consolidated Market Depth**: Multi-venue aggregated bid/ask ladders with depth visualization.
  - **Tab 3: VWAP Slippage Curve**: Execution slippage schedule across order tranches.
  - **Tab 4: Quality & Quarantine Audit**: Detailed record of rejected/quarantined events with exact failure reasons.
  - **Tab 5: OHLCV Candlesticks**: 5-second candle aggregates (Open, High, Low, Close, Volume, Trades).
- Automatic fallback to structured CSV report directories if `openpyxl` is not installed.
- One-command generation and instant launch: `mdrap export AAPL --open`.

### 5. In-Place Live Terminal Ticker & Candlestick Charts (`src/terminal_display.py`)
- **Zero-Scroll In-Place Display**: Updates live market quotes and candlestick charts in-place using ANSI cursor repositioning without cluttering terminal history.
- **High-Resolution Candlestick Visualization**: Renders 3-character columns (` █ `, ` │ `, ` ┼ `) with distinct body margins and box-drawing wicks.
- **Outlier-Resilient Percentile Scaling**: Visual bounds clamped to the 10th–90th price percentiles so extreme anomalies never crush normal candles into a flat line.
- **Aligned Volume Histogram**: Synchronized volume bars underneath each candle column.
- Dedicated modes: full live ticker dashboard (`mdrap live`), ticker-only mode (`mdrap live AAPL --ticker-only`), and standalone historical chart viewer (`mdrap chart AAPL`).

### 6. Synthetic Consolidated NBBO & Multi-Market Connectors (`src/live.py`, `src/bbo.py`)
- Ingests real-time prices across major global crypto exchanges (**Binance, Coinbase, Kraken, OKX, Bybit**) and global equities (**AAPL, MSFT, NVDA, TSLA, SPY, QQQ, GOLD**).
- Computes global tightest bid/ask spread, mid-price, and real-time venue attribution with crossed-market flags.

### 7. Live Watchdog & Automated Source Failover (`src/watchdog.py`)
- Real-time source reliability tracking with silence detection and degradation alerts.
- Automated failover circuit breaker: dynamically evicts silent or corrupt feeds from the consolidated book with hysteresis recovery.

### 8. Immutable Raw Event Archive & Deterministic Replay (`src/archive.py`)
- Date- and source-partitioned write-ahead JSONL log capturing every raw event before processing.
- Deterministic event replay engine allows historical backtesting and auditing through the complete pipeline.

### 9. Enterprise Security & Cryptographic Merkle Audit (`src/security.py`)
- **HMAC-SHA256 Feed Authentication**: Anti-spoofing signature verification with pre-shared feed secrets.
- **Role-Based Access Control (RBAC)**: Privilege boundaries across `VIEWER`, `OPERATOR`, and `ADMIN`.
- **Token Bucket Rate Limiting**: Shields pipeline against denial-of-service and quote flooding ($20,000\text{ eps}$).
- **Tamper-Evident Merkle Audit Log**: Cryptographically chained SHA-256 hash trail in SQLite with standalone verification via `mdrap audit --verify`.

### 10. Binary Shared Memory IPC Transport (`src/shm.py`, `src/protocol.py`)
- Zero-copy lock-free ring buffer for ultra-low latency IPC between the ingestion daemon and trading algorithms.

---

## Architectural Progression & Benchmarks

Measured on identical 10,000-event workloads (`seed=42`) with fixed ground-truth errors:

| Architecture | Throughput (eps) | Proc Latency p50 | Proc Latency Max | Design Highlight |
|---|:---:|:---:|:---:|---|
| **V1 Synchronous Baseline** | **29,402 eps** | **14.6 µs** (14,600 ns) | 255.8 µs | Pure Python, synchronous loop, SQLite batched writes |
| **V2 Decoupled Streaming** | **22,351 eps** | **15.3 µs** (15,300 ns) | 19.9 ms | Multi-threaded in-memory queue bus with backpressure |
| **V4 Native C Hot Path** | **27,274 eps** | **15.7 µs** (15,700 ns) | 265.2 µs | GCC `-O3` ctypes binding with fallback safety |
| **Native C Direct Batch** | **18,669,082 eps** | **50.0 ns** (0.050 µs) | 110.0 ns | Zero-copy SIMD contiguous arrays in CPU L1 cache |

### Latency Hierarchy & Physical Bounds

- **Native C Batch (50.0 ns) vs Pipeline (15.7 µs)**: The **50.0 ns** figure measures the C accelerator alone operating on pre-batched contiguous arrays in CPU L1 cache. The **15.7 µs** figure is the same accelerator measured end-to-end inside the full pipeline (gateway → quality engine → reconciliation → storage).
- **Physics of the "~5 Nanosecond" Myth**: At 4.0 GHz, one CPU cycle is 0.25 nanoseconds; **5 nanoseconds is exactly 20 CPU cycles**. Software running on general-purpose operating systems cannot receive network packets, parse payloads, and evaluate state in 5 nanoseconds (PCIe bus transfer from NIC to RAM alone takes 100–250 ns). Sub-20ns latencies are only physically possible in dedicated **hardware FPGA gate logic** (e.g. AMD Xilinx UltraScale+).

#### The 3 Measurable Platform Latency Tiers
| Tier | Scope / Boundary | Latency (p50) | Throughput | Use Case |
|---|---|:---:|:---:|---|
| **Tier 1: Core C L1 Algorithm** | Isolated Native C rolling math (`fastpath.c`) | **50.0 ns** | 18,669,082 eps | Micro-benchmark core arithmetic |
| **Tier 2: In-Memory Pipeline** | End-to-end stream: gateway + 7 quality rules + BBO | **15.7 µs** | ~63,000 eps | Real-time IPC streaming to bots |
| **Tier 3: Durable Ingest-to-Disk** | Full pipeline with SQLite WAL batched disk persistence | **783.6 µs** | 18,000–22,000 eps | Regulatory audit & persistent storage |

---

## Quick Start

### Installation

Clone the repository and install optional dependencies:
```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap

# MDRAP has ZERO mandatory dependencies (runs 100% on standard library).
# Install optional visualization, financial exporter, and test packages:
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

## CLI Command Reference

| Command | Aliases | Description |
|---|---|---|
| `status` | `s`, `stat` | Show comprehensive platform status overview, database statistics, and engine readiness |
| `shell` | `sh` | Launch low-latency interactive slash-command terminal shell |
| `live` | `stream`, `watch`, `ticker` | Stream live market ticks with in-place updating table & candlestick chart |
| `chart` | `candle`, `graph` | Display visual in-terminal ASCII/Unicode candlestick chart with volume histogram |
| `depth` | `l2`, `book`, `ladder` | Display Consolidated Level-2 Multi-Venue Market Depth Ladder |
| `vwap` | `curve`, `slip` | Compute multi-venue real-time VWAP execution & slippage curves |
| `export` | `exp`, `excel`, `xlsx` | Export market microstructure data to 5-tab Excel (.xlsx) or CSV package |
| `bbo` | `nbbo` | Query Synthetic Consolidated Best Bid & Offer (NBBO) across 5 exchanges |
| `run` | `r` | Run the validation pipeline against the simulator (with live HUD) |
| `benchmark` | `bench`, `b` | Run controlled benchmark and score quality detection against ground truth |
| `compare` | `comp`, `c` | Run V1, V2, and V4 Native C on identical workloads and print comparative report |
| `loadtest` | `load`, `l` | Sweep increasing event volumes (10k to 250k) and report performance trend |
| `stress` | `str` | Run multi-directional stress testing suite and 1M–1B transaction scale analysis |
| `chaos` | `ch` | Execute automated chaos & resilience drills (source kill, network jitter, storage outage) |
| `watchdog` | `w`, `wd` | Show source health status, silence alerts, and automated failover events |
| `security` | `sec` | Display platform security posture, HMAC verification, RBAC, and rate limiting status |
| `keys` | — | Manage client API keys and entitlement tiers (`FREE`, `PRO`, `INSTITUTIONAL`) |
| `audit` | — | View and cryptographically verify tamper-evident Merkle hash audit logs |
| `query` | `q` | Inspect stored SQLite tables: health, latest ticks, lineage trail, and quarantine |
| `replay` | `rep` | Replay archived raw events deterministically through the pipeline |
| `archive` | `arc` | Show immutable raw event JSONL archive statistics |
| `analytics` | `a`, `an` | Query 5s OHLCV candles, bid-ask spreads, and realized volatility |
| `daemon` | `d` | Run headless streaming socket daemon service (Spec §18) |
| `sub` | `subscribe`, `listen` | Subscribe to daemon stream and output formatted ticks or depth to stdout |
| `top` | `mon`, `monitor` | Launch dynamic full-screen terminal service cockpit |
| `test-all` | `test`, `t` | Run all platform CLI commands, benchmarks, queries, and verifications in one pass |

---

## Verification & Testing

MDRAP includes a rigorous test suite of **200 automated unit, integration, security, and chaos tests** covering 100% of pipeline stages:

```bash
# Run the complete automated test suite
pytest tests/ -v
```

```bash
# Run the comprehensive platform verification scorecard
.\mdrap.bat test-all
```

All tests execute with deterministic seeds and verify ground-truth fault detection, boundary conditions, C fallback mechanisms, and zero memory leaks.

---

## Repository Structure

```text
mdrap/
├── cli.py               # Unified CLI, interactive quant shell, and command dispatcher
├── mdrap.bat            # Windows zero-config launcher script
├── build_fastpath.py    # Native C accelerator build script (GCC / Clang / MSVC)
├── config.yaml          # Externalized quality thresholds, anomaly windows & security policies
├── pyproject.toml       # PEP 518/621 project configuration, scripts & package packaging
├── requirements.txt     # Optional runtime & dev dependencies (pure stdlib default)
├── LICENSE              # MIT License
├── .gitignore           # Production-grade gitignore for Python, C artifacts, data, and reports
│
├── src/                 # Core MDRAP Platform Engine
│   ├── analytics.py     # 5s OHLCV candles, bid-ask spread tracking, Welford realized volatility
│   ├── archive.py       # Immutable write-ahead JSONL archive & deterministic replay
│   ├── bbo.py           # Synthetic Consolidated BBO (NBBO) multi-venue engine
│   ├── benchmark.py     # Micro-benchmark harness & ground-truth scoring
│   ├── broker.py        # Thread-safe in-memory streaming bus with backpressure
│   ├── chaos.py         # Automated failure injection & chaos drill suite (Spec §15)
│   ├── client.py        # Low-latency streaming client SDK with reconnect logic
│   ├── config.py        # Central configuration manager & asset-class override resolver
│   ├── dashboard.py     # Real-time terminal pipeline telemetry HUD
│   ├── depth.py         # Consolidated L2 depth aggregation & VWAP slippage curve engine
│   ├── exporter.py      # Institutional 5-tab Excel (.xlsx) & CSV financial model exporter
│   ├── fastpath.c       # Native C hot path accelerator (8,192 symbols, GCC -O3)
│   ├── fastpath.dll     # Pre-compiled high-performance native C shared library
│   ├── fastpath.py      # C ctypes wrapper with transparent pure-Python boundary fallback
│   ├── gateway.py       # Ingestion gateway, timestamp recorder, and schema normalizer
│   ├── live.py          # Multi-exchange connectors (Binance, Coinbase, Kraken, OKX, Bybit, Equities)
│   ├── metrics.py       # High-resolution hardware nanosecond latency & percentile telemetry
│   ├── models.py        # CanonicalEvent, RawEvent, QualityStatus, Reason dataclasses
│   ├── pipeline.py      # V1 synchronous baseline pipeline (ground-truth reference)
│   ├── pipeline_v2.py   # V2 decoupled streaming pipeline with bounded queue broker
│   ├── protocol.py      # Binary serialization & framing protocol for IPC
│   ├── quality.py       # 7-rule data quality evaluation engine (Spec §7)
│   ├── reconciliation.py# Multi-feed cross-reconciliation & dynamic reliability scoring
│   ├── security.py      # HMAC-SHA256 signing, RBAC, Token Bucket rate limiter, Merkle audit log
│   ├── service.py       # Headless streaming daemon, authenticated socket, and service cockpit
│   ├── shm.py           # Lock-free binary shared memory ring buffer IPC
│   ├── simulator.py     # Deterministic feed simulator with seeded anomaly injections
│   ├── storage.py       # Batched SQLite store (canonical, quarantine, lineage, audit, health)
│   ├── stresstest.py    # Multi-directional stress benchmarks & 1B-scale profiling
│   ├── term.py          # Auto-responsive terminal styling with stdlib fallback
│   ├── terminal_display.py # In-place live terminal ticker & ANSI candlestick chart renderer
│   ├── watchdog.py      # Live source watchdog, silence detection & automated failover
│   └── ws_feed.py       # Async WebSocket live market feed connector
│
├── tests/               # 200 Automated Unit & Integration Tests (100% Passing)
│   ├── test_analytics.py
│   ├── test_archive.py
│   ├── test_bbo.py
│   ├── test_chaos.py
│   ├── test_cli.py
│   ├── test_client.py
│   ├── test_config.py
│   ├── test_depth.py
│   ├── test_entitlements.py
│   ├── test_export.py
│   ├── test_fastpath.py
│   ├── test_hardening.py
│   ├── test_live.py
│   ├── test_pipeline_integration.py
│   ├── test_protocol.py
│   ├── test_quality.py
│   ├── test_security.py
│   ├── test_service.py
│   ├── test_shm.py
│   ├── test_stresstest.py
│   ├── test_system_limitations.py
│   ├── test_terminal_display.py
│   ├── test_v2_streaming.py
│   ├── test_vwap.py
│   ├── test_watchdog.py
│   └── test_ws_feed.py
│
├── docs/                # Architecture & Platform Specifications
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
