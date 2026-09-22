# Market Data Reliability & Acceleration Platform (MDRAP)
# Comprehensive Operator & User Manual

> **Platform Mission**: MDRAP is an institutional-grade financial market infrastructure platform designed to ingest, validate, accelerate, and reconcile noisy, delayed, duplicated, and inconsistent market data from disparate exchanges and proprietary feeds into a unified, ultra-low-latency canonical stream with mathematical reliability scoring, cryptographic lineage auditing, and institutional execution analytics.

---

## Table of Contents

1. [Architectural Overview & Physical Bounds](#1-architectural-overview--physical-bounds)
2. [Quickstart & Launcher Methods](#2-quickstart--launcher-methods)
3. [Keyboard-First Speed Ergonomics (Bloomberg / Refinitiv Style)](#3-keyboard-first-speed-ergonomics)
   - [3.1 Ticker-First 2-Token Syntax](#31-ticker-first-2-token-syntax)
   - [3.2 1-Key Quick Launches (Keys 1–9)](#32-1-key-quick-launches-keys-19)
   - [3.3 Single-Letter CLI Shortcuts](#33-single-letter-cli-shortcuts)
   - [3.4 In-Stream Non-Blocking Hotkeys (Live Terminal Controls)](#34-in-stream-non-blocking-hotkeys)
   - [3.5 Intelligent Fuzzy Typo Auto-Correction & Scoped Error Reporting](#35-intelligent-fuzzy-typo-auto-correction--scoped-error-reporting)
4. [Master Command Reference](#4-master-command-reference)
   - [4.1 Market Desk & Live Microstructure](#41-market-desk--live-microstructure)
   - [4.2 Columnar Time-Series Storage & Analytics (DuckDB & Parquet)](#42-columnar-time-series-storage--analytics-duckdb--parquet)
   - [4.3 In-Memory Analytical Engine](#43-in-memory-analytical-engine)
   - [4.4 Platform Health & Real-Time Cockpit](#44-platform-health--real-time-cockpit)
   - [4.5 Validation Pipeline, Benchmarking & Resilience Drills](#45-validation-pipeline-benchmarking--resilience-drills)
   - [4.6 Enterprise Security, API Entitlements & Cryptographic Audit](#46-enterprise-security-api-entitlements--cryptographic-audit)
   - [4.7 Immutable Raw Archive & Deterministic Replay](#47-immutable-raw-archive--deterministic-replay)
   - [4.8 Headless Streaming Socket Daemon & IPC Transports](#48-headless-streaming-socket-daemon--ipc-transports)
   - [4.9 Comprehensive Verification Suite](#49-comprehensive-verification-suite)
   - [4.10 SEC EDGAR Alternative Data & Corporate Research Engine](#410-sec-edgar-alternative-data--corporate-research-engine)
   - [4.11 Global Maritime Tanker & Cargo Tracking Engine](#411-global-maritime-tanker--cargo-tracking-engine)
   - [4.12 Global Multi-Market Infrastructure & International Trading Desk](#412-global-multi-market-infrastructure--international-trading-desk)
5. [Role-Based Workflows](#5-role-based-workflows)
   - [5.1 Quantitative Researchers & Data Scientists](#51-quantitative-researchers--data-scientists)
   - [5.2 Execution Desks & Market Microstructure Traders](#52-execution-desks--market-microstructure-traders)
   - [5.3 Compliance, Surveillance & Risk Officers](#53-compliance-surveillance--risk-officers)
   - [5.4 SRE, Platform & Market Data DevOps Engineers](#54-sre-platform--market-data-devops-engineers)
6. [Configuration Management (`config.yaml`)](#6-configuration-management-configyaml)
7. [Troubleshooting, Physical Constraints & FAQ](#7-troubleshooting-physical-constraints--faq)

---

## 1. Architectural Overview & Physical Bounds

MDRAP operates as a high-throughput financial data pipeline enforcing strict **data segregation**:
- **Raw Events**: Exact wire packets received from exchanges before any parsing.
- **Canonical Events**: Normalized, quality-validated trade and quote ticks.
- **Quarantined Events**: Corrupt, malformed, crossed, or anomalous events segregated with exact cryptographic failure reasons (**never silently dropped**).
- **Derived Analytics**: Consolidated L2 depth ladders, synthetic NBBO, institutional VWAP curves, and SIMD OHLCV candles.

```
                          Multi-Venue Feeds
   [Polygon.io WS]  [Databento DBN]  [Binance/Coinbase WS]  [Feed Simulator]
                          │                 │                       │
                          └────────────┬────┴───────────────────────┘
                                       ▼
                       Streaming Feed Supervisor (Bounded Queue)
                                       │
                                       ▼
                     Gatekeeper: HMAC Auth + Token Bucket RL
                                       │
                                       ▼
            Write-Ahead Raw Archive (Immutable Date/Source JSONL)
                                       │
                                       ▼
                       Gateway & Normalization Engine
                                       │
                                       ▼
            ┌─────────────────────────────────────────────────────┐
            │   7-Rule Data Quality Engine + C Accelerator        │
            │   (Schema, Dedup, Monotonic Gap, Order, Stale,      │
            │    Crossed Quote, 6σ Rolling Welford Math)          │
            │   * Native C Hot Path: 24.4 ns batch / ~50 ns single│
            └──────────────────────────┬──────────────────────────┘
                                       │
                     ┌─────────────────┴─────────────────┐
                     ▼                                   ▼
             [INVALID Events]                    [VALID Events]
             Quarantine Table                Consolidated BBO (NBBO)
             & Signed Audit Log              Multi-Venue L2 Depth & VWAP
                     │                                   │
                     └─────────────────┬─────────────────┘
                                       ▼
                     Durable SQLite Store (WAL Mode)
                                       │
                      Zero-Copy Vectorized Sync (ATTACH)
                                       ▼
                 DuckDB In-Process Columnar Storage Engine
                 (SIMD OHLCV, Vectorized VWAP, Parquet Export)
```

### The 3 Measurable Latency Tiers
MDRAP operates under physical hardware boundaries:

| Tier | Scope / Boundary | Latency (p50) | Max Throughput | Typical Use Case |
|---|---|:---:|:---:|---|
| **Tier 1: Core C L1 Algorithm** | Isolated Native C rolling math (`fastpath.c`) | **50.0 ns** | **18,669,082 eps** | Rolling price variance & duplicate checks in CPU L1 |
| **Tier 2: In-Memory Pipeline** | End-to-end stream: gateway + 7 quality rules + BBO | **15.7 µs** | **~63,000 eps** | Real-time inter-process algorithmic bot feeds |
| **Tier 3: Durable Ingest-to-Disk**| Full pipeline with SQLite WAL batched persistence | **783.6 µs** | **18,000–29,000 eps** | Persistent storage & regulatory audit compliance |

---

## 2. Quickstart & Launcher Methods

### 1. Installation & Environment Setup

MDRAP supports both direct Git execution and standard Pip installation with **automated native C acceleration**:

#### Option A: Clone from Git (Zero-Config JIT Compilation)
```bash
git clone https://github.com/aryan-20-04/mdrap.git
cd mdrap

# Run immediately: on first import, fastpath.py automatically compiles fastpath.c
python cli.py status

# Optional: manually build or test native C shared library
python build_fastpath.py
```

#### Option B: Install via Pip
```bash
# Standard local install
pip install .

# Editable development install
pip install -e .

# Direct from GitHub repository
pip install git+https://github.com/aryan-20-04/mdrap.git
```

> [!TIP]
> Native C acceleration (`fastpath`) automatically detects and compiles via GCC, Clang, or MSVC (`cl.exe`). If no C compiler is present in your environment, MDRAP seamlessly executes with 100% numerical parity using pure Python standard library fallbacks.

### 2. Direct Command Execution
- **Windows**: Use `.\mdrap.bat <command>` or `mdrap <command>`
- **Linux / macOS**: Use `./mdrap <command>`
- **Python**: Use `python cli.py <command>`

### 3. Warm Interactive Quant Shell (Instant Zero-Latency REPL)
Launch without arguments to enter the resident memory shell:
```bash
mdrap
```
The shell keeps database pools, C shared libraries, and network sockets resident in memory, eliminating Python startup overhead and executing commands in sub-milliseconds.

### 4. Help & Command Palette
Type `?` or `help` anywhere in the CLI or shell to display the full visual Command Palette.

---

## 3. Keyboard-First Speed Ergonomics

Designed specifically for institutional traders, quantitative developers, and operations engineers, MDRAP implements four tiers of keyboard-driven ergonomics.

### 3.1 Ticker-First 2-Token Syntax
Inside the interactive shell (`mdrap`) or from the terminal, enter the symbol followed by the single-letter function mnemonic:

| Command | Function | Description |
|---|---|---|
| `AAPL C` | **Candlestick Chart** | Visual ASCII/Unicode technical chart with volume histogram |
| `BTC D` | **Market Depth** | Consolidated Level-2 multi-venue order book ladder |
| `AAPL V` | **VWAP Curve** | Multi-tier institutional VWAP execution & slippage curve |
| `AAPL P` | **Polygon Stream** | Direct Polygon.io WebSocket streaming feed |
| `ES B` | **Databento Stream** | Databento binary DBN high-frequency market stream |
| `AAPL X` | **Financial Export** | 5-tab institutional Excel model (auto-opens workbook) |
| `AAPL` | **Consolidated NBBO** | Instant Best Bid & Offer with venue attribution |

### 3.2 1-Key Quick Launches (Keys 1–9)
In the interactive shell, press a single number key and hit `Enter`:

- `1`: Live BTC/USD Multi-Venue Stream (`live BTC/USD`)
- `2`: Consolidated NBBO Best Bid & Offer (`bbo BTC/USD`)
- `3`: Real-Time Service Ops Cockpit (`top`)
- `4`: Candlestick Technical Chart for AAPL (`chart AAPL`)
- `5`: Consolidated L2 Depth Ladder for BTC/USD (`depth BTC/USD`)
- `6`: Institutional VWAP Curve for BTC/USD (`vwap BTC/USD`)
- `7`: Polygon.io US Equities Stream for AAPL (`live AAPL --feed polygon --mock-feed`)
- `8`: Databento DBN CME Futures Stream for ES (`live ES.c.0 --feed databento --mock-feed`)
- `9`: Comprehensive Platform Status Dashboard (`status`)

### 3.3 Single-Letter CLI Shortcuts
From your terminal (PowerShell, CMD, or bash):
```bash
mdrap c AAPL    # Display Candlestick Chart
mdrap d BTC     # Display Level-2 Depth Book
mdrap v AAPL    # Display Real-Time VWAP Slippage Schedule
mdrap p AAPL    # Stream live Polygon.io feed
mdrap b ES      # Stream live Databento DBN feed
mdrap x AAPL    # Generate 5-Tab Excel Report & Auto-Launch
mdrap AAPL      # Query Best Bid & Offer
```

### 3.4 In-Stream Non-Blocking Hotkeys
During live streaming sessions (`mdrap live`, `mdrap top`, `mdrap depth`), controls are handled non-blocking in <1µs:

- **`[Space]`**: **Instant Freeze / Resume Frame**. Pauses the terminal display and pins a visible `[PAUSED - Press SPACE to resume]` banner so you can inspect microsecond tick prints and depth rungs without scrolling off screen. Pressing `Space` again immediately unfreezes and resumes live updates.
- **`[q]`** or **`[Esc]`**: **Instant Clean Exit**. Restores terminal cursor and returns to the prompt cleanly without Python tracebacks.
- **`[c]`**: **Toggle Candlestick HUD**. Shows or hides the inline technical candle chart.
- **`[d]`**: **Toggle Level-2 Depth Ladder**. Shows or hides the consolidated depth rungs.
- **`[Tab]` / `[1-9]`**: **Switch Focus Ticker**. Cycles or jumps between active universe symbols dynamically on the fly.

### 3.5 Intelligent Fuzzy Typo Auto-Correction & Scoped Error Reporting
To optimize operational speed during live market conditions, MDRAP's CLI parser (`src/cli.py`) incorporates resilient error-handling heuristics:

1. **Subcommand Action Auto-Correction**:
   - Common typos in actions automatically resolve to the closest valid action with an informative notification:
     ```bash
     # User types 'fillings' (double 'l'):
     mdrap edgar fillings NVDA -l 5
     # [mdrap] Notice: Auto-correcting 'fillings' -> 'filings'
     # Executes official SEC filings table for NVDA seamlessly.
     ```
2. **Primary Command Auto-Correction**:
   - Command typos are auto-corrected or suggested:
     ```bash
     mdrap choas  -> Auto-corrects to 'chaos' and executes drills.
     mdrap edgr   -> Auto-corrects to 'edgar'.
     ```
3. **Scoped Error Reporting**:
   - Subcommand argument mistakes print targeted syntax tips and valid choices (e.g. `mdrap edgar filings <TICKER> -l 5`) instead of dumping 50+ lines of generic help text.
4. **Ticker Disambiguation**:
   - Unknown words with close command matches are treated as command typos rather than being erroneously assumed to be unlisted stock tickers.

---

## 4. Master Command Reference

### 4.1 Market Desk & Live Microstructure

#### `mdrap desk` (Aliases: `terminal`, `nav`)
Launch the high-velocity, keyboard-first modal market navigator desk. Inspired by Vim modal controls, Excel grid filtering, and Bloomberg mnemonic velocity, the Navigator Desk lets operators traverse active watchlists, order books, SEC filings, and maritime intelligence with zero mouse latency and built-in two-stage execution safety.

```bash
# Launch the modal keyboard desk
mdrap desk

# Launch directly via alias
mdrap nav
```

**Modal State Machine**:

| Mode | Trigger | Visual Cue | Behavior & Hotkeys |
|---|---|---|---|
| **`NORMAL`** | Default / `Esc` | Bold Green `NORMAL` in header | Home-row directional navigation, tab switching, and one-key analytical inspection. |
| **`FILTER`** | `/` | Bold Yellow `FILTER ('query')` | Live debounce search across current grid rows. Matches symbols, names, or metrics. |
| **`MODAL`** | `b` (Buy) or `s` (Sell) | Blinking Red `CONFIRMATION REQUIRED` | Armed execution ticket. Blocks background mutations until explicitly approved or cancelled. |

**Key Controls Reference**:

- **Grid & Viewport Navigation (`NORMAL` Mode)**:
  - `j` / `[Down]`: Move row selection down.
  - `k` / `[Up]`: Move row selection up.
  - `[Ctrl+D]` / `[PageDown]`: Scroll viewport down one full page.
  - `[Ctrl+U]` / `[PageUp]`: Scroll viewport up one full page.
  - `g` / `[Home]`: Jump directly to the top row.
  - `G` / `[End]`: Jump directly to the bottom row.

- **Workspace Tab Switching**:
  - `l` / `[Right]` / `[Tab]`: Cycle forward to the next workspace tab.
  - `h` / `[Left]`: Cycle backward to the previous workspace tab.
  - `1` – `5`: Direct jump to Tab 1 (Equities/Watchlist), Tab 2 (Crypto/BBO), Tab 3 (L2 Depth), Tab 4 (Filings/Research), Tab 5 (Vessels/Fleet).

- **Analytical Inspection & External Actions**:
  - `[Enter]`: Drilldown on the selected row (opens detailed symbol view or filing context).
  - `c`: Display in-terminal Unicode candlestick chart for the selected symbol.
  - `d`: Display Consolidated Level-2 Market Depth ladder.
  - `v`: Compute real-time VWAP slippage schedule.
  - `x`: Export active grid data to styled Excel (`.xlsx`) workbook.
  - `o`: Open SEC filing or external link directly in the default web browser (OSC 8).
  - `?`: Show modal quick-reference cheatsheet overlay.
  - `q` / `[Esc]`: Cleanly exit the navigator and restore terminal screen buffer.

- **Incremental Search (`FILTER` Mode)**:
  - `/`: Activate search prompt.
  - Any printable character: Appends to filter query and live-prunes the active table.
  - `[Backspace]`: Delete character from search query.
  - `[Enter]`: Lock current filter and return to `NORMAL` mode.
  - `[Esc]`: Discard filter and return to unfiltered view.

- **Two-Stage Armed Execution Safeguard (`MODAL` Mode)**:
  - Pressing `b` (Buy) or `s` (Sell) arms an execution ticket with high-contrast warning banner displaying Symbol, Side, Size, and Order Price.
  - `[Enter]` or `y` / `Y`: Confirm execution and submit order callback.
  - `[Esc]` or `n` / `N` or `q`: Safely abort and disarm ticket without executing.

---

#### `mdrap live [symbol]` (Aliases: `stream`, `watch`, `ticker`, `tick`)
Stream live market events in an in-place updating terminal dashboard with ANSI cursor repositioning and optional technical chart.

```bash
# Stream live crypto multi-venue WebSocket (Binance, Coinbase, Kraken)
mdrap live BTC/USD

# Stream Polygon.io US Equities feed (Quotes, Trades, Aggregate Bars)
mdrap live AAPL --feed polygon --mock-feed

# Stream Databento Binary DBN feed (MBP-1, MBP-10, Trades)
mdrap live ES.c.0 --feed databento --mock-feed

# Limit to 50 ticks or run continuous (default limit 20, use 0 for infinite)
mdrap live AAPL -l 50
```

**Options**:
- `-l, --limit <N>`: Number of ticks to stream (default: 20; `0` for continuous stream).
- `--ws`: Force real-time WebSocket protocol (<1ms push) instead of HTTP polling.
- `--sim`: Drive display using internal multi-venue simulator.
- `--feed {crypto, polygon, poly, databento, dbn, sim}`: Streaming feed source provider.
- `--mock-feed`: Run high-fidelity wire-format mock generator (no external API keys required).
- `--polygon-key <KEY>`: Polygon API key (or set `POLYGON_API_KEY` env var).
- `--databento-key <KEY>`: Databento API key (or set `DATABENTO_API_KEY` env var).
- `--dbn-file <PATH>`: Stream from pre-captured `.dbn` binary capture file.

---

#### `mdrap bbo [symbol]` (Aliases: `nbbo`)
Query the Synthetic Consolidated Best Bid & Offer (NBBO) across connected exchanges.

```bash
# Query NBBO for BTC/USD
mdrap bbo BTC/USD

# Query all tracked instruments across equities and crypto
mdrap bbo all
```

---

#### `mdrap depth [symbol]` (Aliases: `l2`, `book`, `ladder`)
Display the Consolidated Level-2 Multi-Venue Market Depth Ladder.

```bash
# Inspect top 10 depth levels for BTC/USD
mdrap depth BTC/USD -l 10

# Inspect depth for AAPL
mdrap depth AAPL
```

**Output Includes**:
- Unified bid/ask price rungs aggregated across all active exchanges.
- Per-venue size attribution (e.g. `BINANCE: 1.5, COINBASE: 2.2`).
- Cumulative liquidity depth and total notional at each price rung.
- Bid/Ask depth ratio imbalance metric.

---

#### `mdrap vwap [symbol]` (Aliases: `curve`, `slip`, `slippage`)
Compute multi-venue real-time VWAP execution & slippage curves across configurable order tranches.

```bash
# Compute VWAP curve for BTC/USD across standard order tranches (1, 5, 10, 25, 50)
mdrap vwap BTC/USD

# Compute custom sizing tranches
mdrap vwap AAPL --sizes 100 500 1000 5000 10000
```

**Metrics Calculated**:
- Exact executed VWAP price for both BUY and SELL orders.
- Basis point slippage vs NBBO Best Bid / Best Ask.
- Effective spread in bps vs Mid-Price.
- Venue fill routing percentages.
- Cumulative market liquidity within $\pm 10\text{ bps}$, $\pm 50\text{ bps}$, and $\pm 100\text{ bps}$.

---

#### `mdrap chart [symbol]` (Aliases: `candle`, `candlestick`, `graph`)
Display high-resolution ASCII/Unicode candlestick charts directly in your terminal.

```bash
# Display technical chart for AAPL
mdrap chart AAPL

# Custom dimensions
mdrap chart AAPL -w 80 -H 15

# Simulate historical candle stream if database is fresh
mdrap chart AAPL --sim
```

**Features**:
- 3-character candlestick bodies (` █ `, ` │ `, ` ┼ `) with box-drawing wicks.
- 10th–90th price percentile outlier clamping to prevent rogue price anomalies from flattening normal candles.
- Aligned volume histogram bars positioned directly below corresponding candle columns.

---

#### `mdrap export [symbol]` (Aliases: `exp`, `excel`, `xlsx`)
Export comprehensive market microstructure, depth, VWAP curves, and quality audits into an institutional-grade 5-tab Microsoft Excel (`.xlsx`) workbook or structured CSV package.

```bash
# Export AAPL microstructure data and auto-launch in Excel (Windows)
mdrap export AAPL --open

# Export as CSV package instead of Excel
mdrap export AAPL --csv -o data/reports/aapl_package/
```

**Workbook Tabs**:
1. **Executive Summary**: Microstructure KPIs (Volume, VWAP, spreads, crossed quote count, tick count).
2. **Market Depth**: Consolidated L2 bid/ask ladders with depth visualization.
3. **VWAP Curves**: Slippage schedule and venue execution attribution across tranches.
4. **Quality & Quarantine Audit**: Detailed record of rejected/quarantined events with exact failure reasons.
5. **OHLCV Candles**: Resampled 5-second candle aggregates (Open, High, Low, Close, Volume, Trades).

---

#### `mdrap feed` (Aliases: `stream-feed`, `feeds`)
Standalone direct streaming feed inspector and binary packet decoder.

```bash
# Inspect Databento binary stream (decodes MBP-1, MBP-10, Trades)
mdrap feed --source databento --symbols ES.c.0,NQ.c.0 -c 25

# Inspect Polygon.io WebSocket feed
mdrap feed --source polygon --symbols AAPL,MSFT,NVDA -c 50
```

---

### 4.2 Columnar Time-Series Storage & Analytics (DuckDB & Parquet)

The `columnar` command (aliases: `col`, `duck`, `duckdb`) provides in-process SIMD-vectorized historical queries across billions of ticks.

```bash
# 1. Sync operational SQLite ticks to DuckDB columnar storage (zero-copy ATTACH)
mdrap col sync

# 2. Resample trade ticks into OHLCV candles via SIMD arg_min / arg_max
mdrap col ohlcv AAPL --interval 5.0 --limit 15

# 3. Calculate exact institutional VWAP across all stored trade executions
mdrap col vwap AAPL

# 4. Vectorized bid-ask spread analytics & crossed-market anomaly tracking
mdrap col spread all

# 5. Engine latency quantiles (p50, p90, p95, p99, p99.9 in microseconds)
mdrap col latency

# 6. Discrete price-rung volume profile distribution with text bar chart
mdrap col profile AAPL --bins 12

# 7. Export tick dataset to compressed Apache Parquet (zstd / snappy / gzip)
mdrap col export AAPL --compression zstd

# 8. Controlled micro-benchmark: SQLite row scan vs DuckDB columnar SIMD scan
mdrap col bench

# 9. Execute arbitrary SQL queries with rich tabular output
mdrap col sql "SELECT instrument_id, count(*), avg(price) FROM canonical_ticks GROUP BY 1"

# 10. Show columnar engine status and stored tick statistics
mdrap col info
```

---

### 4.3 In-Memory Analytical Engine

The `analytics` command (aliases: `a`, `an`) queries the in-memory analytical storage engine.

```bash
# Query 5-second OHLCV candles for AAPL
mdrap analytics ohlcv AAPL -l 20

# Query bid-ask spread statistics across all instruments
mdrap analytics spread all

# Query realized price volatility (Welford's online variance algorithm)
mdrap analytics vol

# Full market summary
mdrap analytics summary
```

---

### 4.4 Platform Health & Real-Time Cockpit

#### `mdrap status` (Aliases: `s`, `stat`)
Displays the complete platform health summary, database event totals, and engine readiness state.

```bash
mdrap status
```

---

#### `mdrap top` (Aliases: `mon`, `monitor`)
Launches the full-screen dynamic terminal operations cockpit for live platform surveillance.

```bash
# Connect cockpit to local streaming daemon
mdrap top

# Connect with pre-shared security authentication token
mdrap top --token "SEC-SECRET-TOKEN"
```

**Hotkeys Inside Cockpit**:
- `[Space]`: Freeze / unfreeze telemetry display.
- `[q]`: Detach cockpit.

---

#### `mdrap watchdog` (Aliases: `w`, `wd`)
Inspects feed source reputation scores, silence alarms, and automated failover circuit breakers.

```bash
# Display source states (HEALTHY, DEGRADED, SILENT, BLOCKED)
mdrap watchdog status

# View recent watchdog failover alerts
mdrap watchdog alerts -l 20
```

---

#### `mdrap query` (Aliases: `q`)
Inspects stored operational SQLite database tables.

```bash
# Check source health and reputation scores
mdrap query health

# Fetch latest canonical event for an instrument
mdrap query latest AAPL

# Retrieve cryptographic transformation lineage for an event ID
mdrap query lineage "evt-12345"

# Sample quarantined invalid events with failure reasons
mdrap query quarantine 10
```

---

### 4.5 Validation Pipeline, Benchmarking & Resilience Drills

#### `mdrap run` (Aliases: `r`)
Runs the validation pipeline against the simulator with optional live terminal HUD.

```bash
# Standard 50,000-event simulation
mdrap run -e 50000

# Run with custom anomaly fault rates
mdrap run -e 25000 --duplicate-rate 0.02 --price-anomaly-rate 0.01

# Strict DuckDB synchronization (fails with exit code 1 if SQLite and DuckDB diverge)
mdrap run -e 50000 --strict-sync

# Bypass automatic DuckDB synchronization (SQLite-only workflow)
mdrap run -e 50000 --no-sync
```

**Options**:
- `-e, --events <N>`: Total simulated events to generate (default: 10,000).
- `-s, --seed <INT>`: PRNG seed for deterministic reproducible fault injection (default: 42).
- `--strict-sync`: Exit with status code 1 if automatic DuckDB synchronization fails or diverges from SQLite store. Recommended for CI/CD pipelines, cron jobs, and production automation.
- `--no-sync`: Disable automatic post-run DuckDB sync for environments where DuckDB is not needed or locked by another process.
- `--duplicate-rate <FLOAT>`: Fraction of events injected as duplicate bursts.
- `--price-anomaly-rate <FLOAT>`: Fraction of events injected with $>6\sigma$ price jumps.
- `--fastpath`: Enable compiled Native C hot-path accelerator (24.4 ns batch / ~50 ns single).

---

#### `mdrap benchmark` (Aliases: `bench`, `b`)
Executes a rigorous benchmark run, scoring quality detection accuracy against simulated ground truth.

```bash
# Run 100,000-event benchmark with deterministic seed
mdrap benchmark -e 100000 -s 42

# Benchmark Native C Hot Path
mdrap benchmark --fastpath -e 100000 -s 42
```

---

#### `mdrap compare` (Aliases: `comp`, `c`)
Runs identical event workloads across V1 Baseline (Pure Python) and Native C Hot Path (`fastpath`) and outputs a side-by-side performance comparison table.

```bash
mdrap compare -e 100000 -s 42
```

---

#### `mdrap loadtest` (Aliases: `load`, `l`)
Sweeps increasing event volumes (10,000 to 500,000 events) and graphs scaling throughput and latency trends.

```bash
mdrap loadtest --levels 10000,50000,100000,250000
```

---

#### `mdrap stress` (Aliases: `str`)
Executes multi-directional stress tests across gateway normalization, 7 quality rules, synthetic BBO, batched SQLite storage, and IPC ring buffers, with 1M–1B scale extrapolation.

```bash
# Run all stress tests
mdrap stress --module all -e 50000

# Stress specific module
mdrap stress --module quality -e 100000
```

---

#### `mdrap chaos` (Aliases: `ch`)
Executes automated chaos resilience drills (Spec §15) verifying system recovery under extreme conditions.

```bash
# Run all chaos drills (feed outage, network jitter, storage burst)
mdrap chaos all

# Simulate abrupt source outage on FEEDX
mdrap chaos kill --kill-source FEEDX --kill-start 1000 --kill-duration 500
```

---

### 4.6 Enterprise Security, API Entitlements & Cryptographic Audit

#### `mdrap security` (Aliases: `sec`)
Displays platform security posture, HMAC verification status, RBAC entitlement rules, and token bucket rate limits.

```bash
mdrap security
```

---

#### `mdrap keys`
Manages client API keys and entitlement tiers (`FREE`, `PRO`, `INSTITUTIONAL`).

```bash
# List all active client API keys
mdrap keys list

# Create new INSTITUTIONAL tier key with 50,000 eps rate limit
mdrap keys create --client-id "AlphaQuant_LLC" --tier INSTITUTIONAL --rate 50000

# Revoke an API key
mdrap keys revoke --token "MDRAP-KEY-xxxx"
```

---

#### `mdrap audit`
Cryptographically verifies the SHA-256 Merkle hash chain audit log.

```bash
# Verify cryptographic integrity of all audit records in database
mdrap audit --verify

# Export standalone cryptographic proof file for independent verification
mdrap audit --export-proof data/reports/audit_proof_2026.json

# Independently verify standalone proof file without database access
mdrap audit --verify-proof data/reports/audit_proof_2026.json
```

---

### 4.7 Immutable Raw Archive & Deterministic Replay

#### `mdrap archive` (Aliases: `arc`)
Displays storage statistics and date/source partitions of the write-ahead raw event archive.

```bash
mdrap archive
```

---

#### `mdrap replay` (Aliases: `rep`)
Replays archived raw JSONL events deterministically through the validation pipeline for historical auditing and regression testing.

```bash
# Replay all archived events
mdrap replay

# Replay specific date partition
mdrap replay -d 2026-09-05

# Replay specific source feed
mdrap replay -s POLYGON
```

---

#### `mdrap retention` (Aliases: `compact`, `prune`)
Executes database lifecycle pruning and disk space reclamation. Prunes operational ticks older than a configurable retention window while keeping `quarantine` compliance records longer. Runs WAL checkpoint truncation (`TRUNCATE`) and optional SQLite `VACUUM` to reclaim filesystem space. Note that WORM audit tables (`audit_log`, `lineage`) are strictly immutable and never pruned.

```bash
# Prune canonical events >30 days and quarantine >90 days
mdrap retention

# Custom retention windows
mdrap retention --days 14 --quarantine-days 180

# Reclaim filesystem disk pages via full VACUUM
mdrap retention --vacuum

# Machine-readable output for automated maintenance scripts
mdrap retention --json
```

---

### 4.8 Headless Streaming Socket Daemon & IPC Transports

#### `mdrap daemon` (Aliases: `d`)
Runs the high-throughput non-blocking streaming socket daemon service (Spec §18).

```bash
# Run daemon streaming simulated events at 5,000 eps
mdrap daemon --port 9876 --speed 5000

# Run daemon with live exchange feeds and client bearer authentication
mdrap daemon --live --token "MDRAP-SECURE-BEARER-TOKEN"
```

---

#### `mdrap sub [symbol]` (Aliases: `subscribe`, `client`, `listen`)
Subscribes to the running streaming daemon and outputs ticks, depth ladders, or VWAP curves.

```bash
# Subscribe to all live trade and quote ticks
mdrap sub ALL

# Subscribe to Consolidated Level-2 Market Depth ladders
mdrap sub BTC/USD --l2

# Subscribe to real-time institutional VWAP curves
mdrap sub BTC/USD --vwap

# Read from zero-copy shared memory buffer (<1µs latency)
mdrap sub BTC/USD --shm

# Output raw JSON for piping into jq or algorithmic trading bots
mdrap sub BTC/USD -j
```

---

### 4.9 Comprehensive Verification Suite

#### `mdrap test-all` (Aliases: `test`, `t`)
Executes the comprehensive platform verification scorecard:
1. Pytest Unit, Integration, Quantitative, Options, and Fastpath Suite (**620 tests, 100% passing**).
2. V1 Synchronous Baseline Pipeline Run.
3. V2 Decoupled Streaming Bus Run.
4. Native C Hot Path Accelerator Run.
5. Storage & Lineage Cryptographic Verification.
6. Chaos Feed Outage Injection.

```bash
mdrap test-all
```

#### Running Pytest in Dual Execution Modes
MDRAP enforces rigorous verification across both compiled native C and pure Python execution paths:

- **Mode A: With FastPath (Default Production Mode)**
  ```bash
  python -m pytest tests/ -q
  # Result: 620 passed in ~69s (0 failed, 0 skipped)
  ```

- **Mode B: Without FastPath (Pure Python Fallback Mode)**
  ```bash
  # PowerShell:
  $env:MDRAP_DISABLE_FASTPATH="1"; python -m pytest tests/ -q; Remove-Item Env:\MDRAP_DISABLE_FASTPATH

  # Bash / Linux / macOS:
  MDRAP_DISABLE_FASTPATH=1 python -m pytest tests/ -q
  # Result: 597 passed, 7 skipped in ~70s
  ```

#### `mdrap throughput` (Aliases: `tp`, `meps`, `million`)
Empirical vectorized Native C SBE validation benchmark targeting 500,000 to 1,000,000+ events/sec:
```bash
# Benchmark 1,000,000 events with architectural progression comparison
mdrap throughput -e 1000000 --compare
```

---

### 4.10 SEC EDGAR Alternative Data & Corporate Research Engine

MDRAP integrates directly with the U.S. Securities and Exchange Commission (SEC) EDGAR REST API and XBRL database to deliver real-time corporate intelligence, executive moves, insider transactions, and audited financial facts.

Protected by strict SSRF guards, XXE/Billion-Laughs mitigation, zero local path disclosures, and a 300s TTL multi-tier cache (`data/edgar_cache/`).

#### `mdrap edgar profile [ticker]` (Aliases: `mdrap company [ticker]`)
Retrieves corporate identity, Central Index Key (CIK), Standard Industrial Classification (SIC), fiscal year-end, and recent filings indexed.
```bash
mdrap edgar profile AAPL
# Shorthand:
mdrap company NVDA
```

#### `mdrap edgar events [ticker]` (Aliases: `mdrap events [ticker]`)
Decodes official Form 8-K filings into plain-English event triggers classified by urgency:
- **`CRITICAL` / `HIGH`**: Item 5.02 (Executive/Director departure or election), Item 1.01 (Material agreements), Item 2.02 (Results of operations/earnings), Item 1.03 (Bankruptcy/receivership).
- **`MEDIUM` / `INFO`**: Item 7.01 (Reg FD disclosure), Item 5.07 (Shareholder voting results), Item 9.01 (Financial exhibits).
```bash
mdrap edgar events TSLA --limit 10
# Force fresh network pull bypassing local cache:
mdrap edgar events AAPL --fresh
```

#### `mdrap edgar insiders [ticker]` (Aliases: `mdrap insiders [ticker]`)
Parses official Form 4 XML filings to inspect open-market purchases, sales, stock awards, and option exercises executed by corporate officers, directors, and 10%+ beneficial owners.
```bash
mdrap edgar insiders MSFT --limit 15
```

#### `mdrap edgar facts [ticker] [-m METRIC]`
Extracts audited GAAP figures directly from SEC XBRL filings with fiscal periods and reporting forms.
```bash
mdrap edgar facts AAPL --metric Revenues
mdrap edgar facts GOOGL --metric NetIncomeLoss
```

#### `mdrap edgar filings [ticker] [-t FORM_TYPE]`
Lists recent official filings with direct HTTPS links to official SEC accession folders.
```bash
mdrap edgar filings AAPL -t 10-K
mdrap edgar filings NVDA -t 8-K --limit 20

# Open the top filing directly in default web browser (-o / --open)
mdrap edgar filings NVDA -l 5 -o
mdrap edgar profile MSFT -o
```


---

### 4.11 Global Maritime Tanker & Cargo Tracking Engine

Tracks commercial crude oil tankers (VLCC/ULCC), LNG carriers, dry bulk carriers, and container ships transiting critical energy bottlenecks. Maps physical supply chain flows to financial instruments and commodity futures.

Accelerated via the **Tier-2 Native C Vectorized Geodesic Fastpath** (`fastpath.c`), which evaluates 10,000 vessels across all global chokepoints in **23.08 ms (~288 ns per chokepoint check)** using Axis-Aligned Bounding Box (AABB) spatial pre-filtering.

#### `mdrap vessel list` (Aliases: `mdrap tankers`, `mdrap vessels`, `mdrap cargo`)
Displays the global commercial fleet with vessel type, operating fleet owner, chartering major, commodity payload, and load status (`LADEN` vs `BALLAST`).
```bash
# List all active vessels
mdrap vessel list

# Filter by vessel type and operating entity
mdrap vessel list -t tanker -c Frontline

# Filter by laden status and chokepoint
mdrap vessel list -s laden -k hormuz
```

#### `mdrap vessel track <IDENTIFIER>` (Aliases: `mdrap vessel <NAME>`)
Generates a comprehensive commercial and navigational intelligence dossier by vessel IMO, MMSI, or Name:
```bash
mdrap vessel track "FRONT ALTAIR"
# Shorthand:
mdrap vessel "TI EUROPE"
```

#### `mdrap vessel chokepoints`
Monitors the 8 primary geopolitical maritime chokepoints (Strait of Hormuz, Strait of Malacca, Suez Canal, Bab-el-Mandeb, Panama Canal, Bosphorus, Cape of Good Hope, Dover Strait) with daily flow volumes and active vessels within 150 nautical miles.
```bash
mdrap vessel chokepoints
```

#### `mdrap vessel commodities`
Breaks down seaborne cargo exposures across Crude Oil, Refined Products, LNG, Dry Bulk, and Container Cargo, tagging active chartering commodity majors (Saudi Aramco, Shell, BP, Vitol, Trafigura, Vale).
```bash
mdrap vessel commodities
```

---

### 4.12 Global Multi-Market Infrastructure & International Trading Desk

MDRAP provides native first-class support for international market microstructure, ISO 10383 venue identification, multi-currency portfolio accounting, universal symbology resolution, and venue-aware data quality validation across major global financial hubs:
- 🇮🇳 **India**: National Stock Exchange (`XNSE`) & Bombay Stock Exchange (`XBOM`) — INR (₹), Lakh/Crore grouping, 09:15–15:30 IST session, pre-market call auction, dynamic tick tiers (₹0.01 / ₹0.05), stock/index circuit filters.
- 🇩🇪 **Germany**: Deutsche Börse Xetra (`XETR`) & Eurex (`XEUR`) — EUR (€), 09:00–17:30 CET continuous trading, MiFID II RTS 28 liquidity tick bands, dynamic and static volatility interruptions.
- 🇯🇵 **Japan**: Tokyo Stock Exchange (`XTKS` / JPX) & Osaka Exchange (`XOSE`) — JPY (¥, 0 decimals), 4-digit / alphanumeric ticker codes (e.g. `7203`, `6758`), TOPIX100 tiered tick sizes, Tokuhai (Special Quote indication) order matching halts.
- 🇬🇧 **United Kingdom**: London Stock Exchange (`XLON`) — GBP (£), FTSE 100 benchmark.
- 🇭🇰 **Hong Kong**: Hong Kong Exchanges and Clearing (`XHKG`) — HKD (HK$), Hang Seng benchmark.
- 🇺🇸 **United States**: Nasdaq (`XNAS`) & New York Stock Exchange (`XNYS`) — USD ($), Reg NMS continuous trading.

---

#### `mdrap markets` (Aliases: `venues`, `world`, `desk`)
Displays the unified global trading desk clock, real-time session phase state machine (`PRE_OPEN`, `CONTINUOUS`, `VOLATILITY_HALT`, `CLOSING_AUCTION`, `POST_CLOSE`, `CLOSED`), time remaining to next phase transition, benchmark index performance, tick size models, and circuit breaker tolerances.

```bash
# Display global exchange trading desk clock and session phases
mdrap markets

# Aliases
mdrap venues
mdrap world
mdrap desk
```

---

#### Multi-Currency Portfolio Accounting
MDRAP's portfolio and paper execution engines support multi-currency positions and cross-currency portfolio valuation via the institutional triangular `FXMatrix` engine (`src/fx.py`).

```bash
# View portfolio with localized conversion to Indian Rupees (INR Lakh/Crore formatting: ₹1,25,000.00)
mdrap portfolio --currency INR

# View portfolio in Euros (EUR €)
mdrap portfolio --currency EUR

# View portfolio in Japanese Yen (JPY ¥ zero-decimal formatting)
mdrap portfolio --currency JPY

# View portfolio in British Pounds (GBP £)
mdrap portfolio --currency GBP
```

---

#### Universal Symbology Resolution
The universal symbology engine (`src/symbology.py`) resolves disparate ticker formats into canonical instrument identifiers with venue, currency, and asset class mappings:
- **Exchange Suffixes**: `RELIANCE.NS` → `XNSE` (INR), `TCS.BO` → `XBOM` (INR), `BMW.DE` → `XETR` (EUR), `7203.T` → `XTKS` (JPY).
- **Japanese 4-Digit Security Codes**: `7203` → Toyota Motor Corp (`XTKS`), `6758` → Sony Group (`XTKS`), `9984` → SoftBank Group (`XTKS`).
- **Reuters Instrument Codes (RIC)**: `RELI.NS` → `RELIANCE` on `XNSE`, `BMWG.DE` → `BMW` on `XETR`.
- **Bloomberg Tickers**: `RELIANCE:IN`, `BMW:GR`, `7203:JP`.
- **ISIN Codes**: `INE002A01018` (Reliance), `DE0005190003` (BMW), `JP3633400001` (Toyota).
- **Named Corporate Aliases**: `TOYOTA`, `SONY`, `NINTENDO`, `RELIANCE`, `TCS`, `INFOSYS`, `BMW`, `SIEMENS`, `SAP`.

---

#### Multi-Market Simulation Profiles
Run the validation and reconciliation pipeline seeded with realistic market microstructure profiles:

```bash
# Simulate Indian equity market (NSE) with INR pricing and 0.05 tick sizes
mdrap run --market nse -e 50000

# Simulate German equity market (XETR) with EUR pricing and MiFID II tick bands
mdrap run --market xetra -e 50000

# Simulate Japanese equity market (TSE) with JPY pricing and TOPIX100 tiers
mdrap run --market tse -e 50000

# Simulate Global cross-market composite (US + India + Germany + Japan)
mdrap run --market global -e 50000
```

---

#### Venue-Aware Microstructure Quality Validation
In international markets, extreme price movements are often regulated by exchange mechanisms rather than representing corrupt feed anomalies:
- **`CIRCUIT_FILTER_BREACH`**: Triggered when Indian securities or indices on `XNSE`/`XBOM` exceed statutory daily bands (10%, 15%, 20%).
- **`VOLATILITY_INTERRUPTION`**: Triggered when German securities on `XETR`/`XEUR` exceed dynamic or static corridor limits ($\ge 5\%$), initiating a volatility call auction.
- **`SPECIAL_QUOTE_INDICATION`**: Triggered when Japanese securities on `XTKS`/`XOSE` enter a Tokuhai order-imbalance condition ($\ge 8\%$), soliciting liquidity before trade execution.
- **Standard Anomaly Fallback**: Unrecognized or US venue events continue to be tagged as `PRICE_ANOMALY`.

---

## 5. Role-Based Workflows

### 5.1 Quantitative Researchers & Data Scientists
1. **Sync Raw Operational Ticks to Columnar Storage**:
   ```bash
   mdrap col sync
   ```
2. **Resample Millisecond Data into Multi-Interval OHLCV Bars**:
   ```bash
   mdrap col ohlcv AAPL -i 1.0 -l 100
   mdrap col ohlcv AAPL -i 5.0 -l 100
   ```
3. **Analyze Realized Volatility & Bid-Ask Spreads**:
   ```bash
   mdrap col spread AAPL
   mdrap analytics vol
   ```
4. **Export Clean Dataset to Compressed Apache Parquet for Pandas / Polars**:
   ```bash
   mdrap col export AAPL --compression zstd
   # Read directly in Python:
   # df = pd.read_parquet("data/parquet/aapl_ticks.parquet")
   ```

---

### 5.2 Execution Desks & Market Microstructure Traders
1. **Launch Resident Terminal Shell**:
   ```bash
   mdrap
   ```
2. **Inspect Consolidated Level-2 Book**:
   ```text
   > BTC D
   ```
3. **Evaluate Execution Slippage Curves for Block Sizing**:
   ```text
   > BTC V
   ```
4. **Stream Live Market Ticks with Non-Blocking Controls**:
   ```text
   > 1
   ```
   - Press `[Space]` to freeze the frame when large orders hit the book.
   - Press `[c]` to toggle candlestick HUD.
   - Press `[q]` to return to shell.
5. **Export Microstructure Model to Excel for Client Reporting**:
   ```text
   > AAPL X
   ```

---

### 5.3 Compliance, Surveillance & Risk Officers
1. **Inspect Quarantined Data & Failure Reasons**:
   ```bash
   mdrap query quarantine 25
   ```
2. **Trace Exact Cryptographic Lineage for a Contested Event**:
   ```bash
   mdrap query lineage "evt-12345"
   ```
3. **Verify Merkle Hash Chain Audit Integrity**:
   ```bash
   mdrap audit --verify
   ```
4. **Export Signed Regulatory Proof Package**:
   ```bash
   mdrap audit --export-proof "data/reports/regulatory_proof_2026.json"
   ```

---

### 5.4 SRE, Platform & Market Data DevOps Engineers
1. **Launch Background Daemon Service**:
   ```bash
   mdrap daemon --host 0.0.0.0 --port 9876 --live --token "SECRET"
   ```
2. **Monitor Live Operations in Ops Cockpit**:
   ```bash
   mdrap top --token "SECRET"
   ```
3. **Inspect Feed Failover & Watchdog Circuit Breakers**:
   ```bash
   mdrap watchdog status
   mdrap watchdog alerts -l 50
   ```
4. **Execute Chaos Resilience Drills**:
   ```bash
   mdrap chaos all
   ```
5. **Run Full Regression Verification**:
   ```bash
   mdrap test-all
   ```

---

## 6. Configuration Management (`config.yaml`)

Platform thresholds, anomaly detection windows, and security policies are managed via `config.yaml`:

```yaml
# ==============================================================================
# MDRAP Platform Configuration
# ==============================================================================

pipeline:
  version: "v1"                    # Synchronous baseline pipeline
  batch_size: 1000                 # SQLite batch commit threshold

quality:
  stale_threshold_seconds: 5.0     # Max event delay before STALE flag
  crossed_quote_check: true        # Flag if Bid > Ask
  price_anomaly_sigma: 6.0         # Price jump threshold (standard deviations)
  price_window_size: 100           # Rolling Welford variance window
  out_of_order_tolerance: 0.1      # Timestamp jitter tolerance (seconds)

reconciliation:
  consensus_method: "weighted"     # Multi-feed consensus algorithm
  min_reputable_score: 0.85        # Minimum feed score to participate in NBBO

watchdog:
  silence_threshold_seconds: 2.0   # Silence cutoff for feed disconnect
  degradation_threshold: 0.90      # Minimum reliability score before DEGRADED
  recovery_threshold: 0.93         # Hysteresis recovery score threshold

columnar:
  db_path: "data/mdrap.duckdb"     # DuckDB database file
  parquet_dir: "data/parquet"      # Directory for exported Parquet files
  threads: 4                       # Vectorized SIMD worker threads
  memory_limit: "2GB"              # Max RAM allocated to analytical engine

security:
  rate_limit_eps: 20000.0          # Global token bucket rate limit per IP/Key
  audit_hash_chain: true           # Enable cryptographic Merkle chaining
```

---

## 7. Troubleshooting, Physical Constraints & FAQ

### Q1: Why does `mdrap col ohlcv AAPL` show empty candles initially?
**Answer**: DuckDB acts as an analytical cache. If you have ingested ticks into SQLite (`data/mdrap.db`) but have not yet synced to DuckDB, run:
```bash
mdrap col sync
```
This performs a zero-copy vectorized scan (`ATTACH TYPE SQLITE`), synchronizing 300,000+ ticks in ~2.8 seconds. Subsequent commands auto-sync if DuckDB is detected to be empty.

### Q2: Why is the Native C accelerator latency reported as 50.0 ns in benchmarks but 15.7 µs in the pipeline?
**Answer**:
- **50.0 ns (50 nanoseconds, 18.6M eps)** measures the isolated C algorithm (`fastpath.c`) operating on pre-allocated, SIMD-aligned contiguous memory arrays inside the **CPU L1 cache**.
- **15.7 µs (15,700 nanoseconds, ~63,000 eps)** measures the entire end-to-end software pipeline: Python object creation, gateway timestamping, dictionary hashing, 7 quality rules, NBBO calculation, and thread dispatch.
- **Physical Reality**: Sub-20ns latencies are only physically possible on dedicated **hardware FPGA gate arrays** (e.g. AMD Xilinx UltraScale+). General-purpose CPUs running general-purpose operating systems incur 100–250 ns of latency solely transferring packets from the NIC across the PCIe bus into system RAM.

### Q3: How does the system handle corrupt or out-of-sequence events?
**Answer**: In accordance with Design Principle §3: **Never silently discard bad data**. Every corrupt, duplicate, out-of-order, or anomalous event is tagged with its non-downgradable quality status (`INVALID`), assigned failure codes (`Reason.DUPLICATE`, `Reason.CROSSED_QUOTE`, `Reason.PRICE_SPIKE`), and routed to the `quarantine` database table with full cryptographic lineage tracing.

### Q4: How do I cleanly exit a live streaming session?
**Answer**: Press `[q]` or `[Esc]`. The non-blocking keyboard poller detects the keystroke in <1µs, restores the ANSI terminal cursor, flushes pending database batches, and cleanly returns to your shell prompt without throwing Python `KeyboardInterrupt` stack traces.


## CryptoHFTData historical data

Use `mdrap historical` (aliases `history`, `chd`) for symbol discovery, hourly
file planning, native Parquet downloads and canonical historical imports. See
the [CHD guide](CHD.md) for installation, UTC intervals, snapshot warmup,
authentication, provenance and complete CLI/Python examples.

---

## 8. Diagnostics, Configuration & Extension Points (Round 2)

### Platform Health & Integrity Diagnostics (`mdrap doctor`)
Run comprehensive self-checks across Python environment, compiler detection on PATH, active engine tier, configuration SHA-256, SQLite WAL status, and execute a 10,000-event smoke benchmark:
```bash
mdrap doctor
```

### Self-Contained Interactive Demo (`mdrap demo`)
Stream 50,000 synthetic market events into SQLite WAL storage and automatically launch the keyboard-first modal desk navigator:
```bash
mdrap demo
```

### Layered Configuration Inspection (`mdrap config show`)
Inspect resolved data quality thresholds and track origin provenance (`defaults -> venue -> instrument_class -> instrument`):
```bash
# Query global defaults
mdrap config show

# Query specific venue and instrument overrides
mdrap config show --venue binance --instrument BTCUSDT --json
```

### Table Export to Parquet, JSON, and CSV (`mdrap export`)
Export canonical market data or quarantined anomalies from SQLite into compressed Parquet, JSON, or CSV:
```bash
# Export to Apache Parquet (requires optional pyarrow)
mdrap export AAPL --format parquet --output canonical.parquet

# Export to JSON
mdrap export AAPL --format json --output canonical.json
```

### Custom Feed Adapters and User Rules (Bits 32–63)
To ingest from proprietary venues or brokers, implement the `FeedAdapter` protocol and register custom quality rules:
```python
from adapters import FeedAdapter
from rules import register_rule
from models import CanonicalEvent, QualityStatus

@register_rule(bit=32, name="CUSTOM_WIDE_SPREAD", severity=QualityStatus.SUSPICIOUS)
def check_spread(event: CanonicalEvent) -> bool:
    if event.bid_price and event.ask_price:
        return (event.ask_price - event.bid_price) > 5.0
    return False
```
See `examples/custom_venue/` for a complete runnable walkthrough.

---

## 9. HFT-Tier Latency Architecture & Standalone Core (Round 3)

### Standalone Native Core (`mdrap core`)
Execute the decoupled native C hot-path engine (`mdrap-core`) wire-to-SHM with zero Python runtime or GIL overhead:
```bash
# Run 100k events through native core into shared memory
mdrap core --events 100000

# Specify custom SHM segment name and rate throttling
mdrap core --events 50000 --shm mdrap_feed --rate 10000 --symbol BTC/USD

# Force compilation of binary before running
mdrap core --build --events 10000
```

### Hardware Timestamping & Diagnostic Doctor (`mdrap doctor`)
Inspect environment, compiler availability, active engine tier, binary presence, and clock source precision:
```bash
mdrap doctor
```
On Linux, `mdrap doctor` detects `SO_TIMESTAMPING` support and Linux PTP Hardware Clock devices (`/dev/ptp*`). On Windows, it reports software QPC (`QueryPerformanceCounter`, ~100ns precision).

### Multi-Source Contention Benchmarking
Benchmark lock-free single-writer SPSC ingestion against traditional mutex-locked multi-source ingestion across 1, 2, 4, and 8 concurrent sources:
```bash
python benchmarks/bench_contention.py
```
Outputs throughput (eps) and tail latency percentiles (p50, p95, p99, p99.9), saving statistical results to `benchmarks/contention_results.json`.

### Tier 2 FPGA Hardware Learning Track (`fpga/`)
For researchers exploring hardware-accelerated tick-to-trade appliances, MDRAP includes synthesizable Verilog-2001 modules implementing combinatorial quality rules in pure gate logic:
- **`fpga/mdrap_crossed_quote.v`**: 64-bit carry-chain comparator evaluating $Bid \ge Ask$ in 1 clock cycle (~3.3 ns @ 300 MHz).
- **`fpga/mdrap_sequence_gap.v`**: Monotonic gap and retrograde arrival validator in 1 clock cycle (~3.3 ns).
- **`fpga/tb_mdrap_rules.v`**: Self-checking Verilog testbench.

To verify cycle-accurate bit-exact parity against Python and C software engines:
```bash
pytest tests/test_fpga_parity.py -v
```
See [FPGA Spike Findings](fpga-spike-findings.md) for timing closure, resource utilization, and the gap analysis to commercial hardware appliances.


