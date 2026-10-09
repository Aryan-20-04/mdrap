# MDRAP CLI Reference Manual

Complete, authoritative reference for the `mdrap` command-line interface, generated directly from the live `argparse` definition.

Total commands documented: **69**.

## Global Options

| Flag | Description |
| --- | --- |
| `--json` | Output structured JSON instead of formatted tables. |
| `--pretty` | Force rich terminal UI formatting even in non-interactive/redirected contexts. |
| `--no-color`, `--plain` | Suppress ANSI colors and styling (honors `NO_COLOR=1`). |
| `-h`, `--help` | Display help and exit. |

## Command Index

| Command | Aliases | Description |
| --- | --- | --- |
| [`alert`](#alert) | `alerts` | Real-time alert engine (price, spread, volume, drawdown) |
| [`analytics`](#analytics) | `a` | Query OHLCV candles, bid-ask spreads, and realized volatility |
| [`arbitrate`](#arbitrate) | `arb` | Run dual-path Multicast UDP A/B feed arbitration and TCP replay test (§18, §26) |
| [`archive`](#archive) | `arc` | Show raw event archive statistics |
| [`audit`](#audit) | *none* | View and cryptographically verify tamper-evident audit logs |
| [`backtest`](#backtest) | `bt` | Historical backtesting engine with point-in-time event replay |
| [`bars`](#bars) | `bardb`, `candles` | Multi-timeframe OHLCV bar database and historical lookup |
| [`bbo`](#bbo) | `nbbo` | Query Synthetic Consolidated Best Bid & Offer (NBBO) |
| [`benchmark`](#benchmark) | `bench` | Run controlled benchmark and score quality detection |
| [`chaos`](#chaos) | `ch` | Execute automated chaos & resilience drills (§15) |
| [`chart`](#chart) | `candle` | Display visual in-terminal ASCII/Unicode candlestick chart |
| [`columnar`](#columnar) | `col` | Query high-performance DuckDB columnar time-series storage & analytics (Phase 3) |
| [`compare`](#compare) | `comp` | Run V1 Pure Python vs V1 Native C on identical workloads and compare |
| [`completion`](#completion) | `complete` | Generate shell autocompletion script (bash, zsh, fish, powershell) |
| [`config`](#config) | `cfg` | Inspect and query hierarchical mdrap.toml configuration |
| [`core`](#core) | `fast-core`, `t1` | Run standalone native C hot-path engine (T1 zero-lock tier) |
| [`corpact`](#corpact) | `dividends`, `splits` | Corporate actions processor: splits, dividends, ticker changes |
| [`daemon`](#daemon) | `d` | Run headless streaming socket daemon service (§18) |
| [`dashboard`](#dashboard) | `dash` | Launch real-time terminal visualizer dashboard |
| [`deadletter`](#deadletter) | *none* | Inspect and replay uncommitted dead-letter transaction logs |
| [`demo`](#demo) | `dm` | Execute bundled 50k-event run and open live desk navigator |
| [`depth`](#depth) | `l2` | Show Consolidated Level-2 Multi-Venue Market Depth Ladder |
| [`desk`](#desk) | `nav` | Launch interactive keyboard-first modal desk navigator (Vim/Excel ergonomics) |
| [`doctor`](#doctor) | `doc` | Inspect environment, compiler, engine tier, WAL status, and run 10k smoke check |
| [`edgar`](#edgar) | `filings` | SEC EDGAR Alternative Data: 8-K material events, Form 4 insiders, GAAP facts |
| [`export`](#export) | `exp` | Export market microstructure data to Excel (.xlsx) or CSV |
| [`failover`](#failover) | `fo` | Active-Passive cluster failover coordinator |
| [`features`](#features) | `feat` | ML feature store: technical indicators and microstructure metrics |
| [`feed`](#feed) | `feeds` | Inspect, benchmark, and test streaming feeds (Polygon, Databento, Crypto WS) |
| [`flow`](#flow) | `cvd` | Track Institutional Order Flow, Lee-Ready Aggressor Side, CVD & MPID Net Deltas |
| [`gateway`](#gateway) | `gw` | Launch AsyncIO TCP Gateway for external clients |
| [`historical`](#historical) | `chd`, `history` | CHD historical data: discover, download and ingest |
| [`itch`](#itch) | `totalview` | NASDAQ TotalView-ITCH 5.0 Binary Feed Engine & Global Benchmark |
| [`keys`](#keys) | *none* | Manage client API keys and authentication tokens |
| [`lake`](#lake) | `partitions`, `store-hist` | Manage partitioned historical market data store |
| [`live`](#live) | `stream` | Stream live market ticks with in-place updating table & candlestick chart |
| [`loadtest`](#loadtest) | `load` | Sweep increasing event volumes and report trend |
| [`markets`](#markets) | `venues`, `world` | Global financial exchange directory, market clocks, and microstructure |
| [`mbo`](#mbo) | `l3` | Inspect Level-3 Market-By-Order (MBO) FIFO queue ranks and L2 book projection (§18, §26) |
| [`news`](#news) | `sentiment` | Financial news aggregation, sentiment analysis, and entity extraction |
| [`options`](#options) | `greeks`, `opt` | Options pricing models, Greeks chain, and implied volatility solver |
| [`plugins`](#plugins) | *none* | List installed plugins and extension entry points |
| [`portfolio`](#portfolio) | `port` | Portfolio tracker with P&L attribution and benchmark comparison |
| [`query`](#query) | `q` | Inspect stored data: health, latest, lineage, quarantine |
| [`replay`](#replay) | `rep` | Replay archived raw events through the pipeline |
| [`report`](#report) | `reg` | Generate institutional fund regulatory compliance reports (SEC 13F, MiFID II RTS 28) |
| [`retention`](#retention) | `prune` | Run storage retention compaction and disk reclamation |
| [`risk`](#risk) | `cvar`, `var` | Portfolio risk management, VaR, CVaR, and correlation analysis |
| [`run`](#run) | `r` | Run the pipeline against the simulator (optionally with live dashboard) |
| [`schedule`](#schedule) | `cron`, `sched` | Scheduled tasks and automated EOD reports |
| [`sdk-demo`](#sdk-demo) | `sdk` | Run Quant-Ready Python SDK Client Demo |
| [`security`](#security) | `sec` | Display platform security posture, HMAC verification, RBAC, and rate limiting status |
| [`serve`](#serve) | `api` | Start production REST API and WebSocket event streaming server |
| [`shell`](#shell) | `sh` | Launch low-latency interactive slash-command shell |
| [`simulate`](#simulate) | `sim` | Simulate concurrent multi-device normal vs fast-paced user workloads (§26) |
| [`status`](#status) | `s` | Show comprehensive platform status overview |
| [`strategy`](#strategy) | `strat` | Institutional Algorithmic Strategy Engine & Paper EMS (§26) |
| [`stress`](#stress) | `str` | Run multi-directional stress tests and 1M to 1B scale analysis |
| [`sub`](#sub) | `subscribe` | Subscribe to daemon stream and output ticks or depth to stdout |
| [`tca`](#tca) | `bestex` | Run Institutional Best Execution & TCA Slippage Engine with Merkle Proofs |
| [`test-all`](#test-all) | `t` | Run all CLI tests, benchmarks, queries, and validations in one place |
| [`throughput`](#throughput) | `tp` | Benchmark 500,000 to 1,000,000+ events/sec on vectorized Native C SBE stream (§26) |
| [`top`](#top) | `mon` | Launch dynamic full-screen terminal service cockpit |
| [`version`](#version) | `v` | Show MDRAP version |
| [`vessel`](#vessel) | `ais` | Maritime Tanker & Cargo Tracking: Crude oil, LNG, bulk, and container tracking |
| [`vwap`](#vwap) | `curve` | Compute multi-venue real-time VWAP execution & slippage curves |
| [`wal`](#wal) | `log` | Verify, inspect, or salvage IngestLog Write-Ahead Log segments |
| [`watchdog`](#watchdog) | `w` | Show source health status and watchdog alerts |
| [`watchlist`](#watchlist) | `wl` | Named watchlist management |

---

## Subcommand Details

### `alert`

**Description:** Real-time alert engine (price, spread, volume, drawdown)

**Aliases:** `alerts`

**Usage:** `mdrap alert [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `-i`, `--symbol` |  (default: `AAPL`) |
| `-t`, `--type` |  (default: `ABOVE`) |
| `-v`, `--target` |  (default: `200.0`) |
| `--db` |  (default: `data/alerts.db`) |

---

### `analytics`

**Description:** Query OHLCV candles, bid-ask spreads, and realized volatility

**Aliases:** `a`

**Usage:** `mdrap analytics [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action: ohlcv, spread, vol, summary |
| `target` | Instrument symbol (e.g. AAPL, MSFT, all) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--ohlcv` | Show OHLCV candles for an instrument |
| `--spread` | Show bid-ask spread analysis (use 'all' for all instruments) |
| `--volatility` | Show realized volatility by instrument |
| `--summary` | Show market analytics summary |
| `-l`, `--limit` | Row limit (default: `20`) |

---

### `arbitrate`

**Description:** Run dual-path Multicast UDP A/B feed arbitration and TCP replay test (§18, §26)

**Aliases:** `arb`

**Usage:** `mdrap arbitrate [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `-e`, `--events` | Number of dual-line events to simulate (default: 500) (default: `500`) |
| `--drop-a` | Packet drop rate on Feed A (default: 0.05) (default: `0.05`) |
| `--drop-b` | Packet drop rate on Feed B (default: 0.05) (default: `0.05`) |

---

### `archive`

**Description:** Show raw event archive statistics

**Aliases:** `arc`

**Usage:** `mdrap archive [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--base-dir` | Archive directory (default: `data/raw_archive`) |

---

### `audit`

**Description:** View and cryptographically verify tamper-evident audit logs

**Usage:** `mdrap audit [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--verify` | Cryptographically verify SHA-256 Merkle chain integrity |
| `--export-proof` | Export cryptographic audit trail as an independently verifiable JSON proof |
| `--verify-proof` | Independently verify a standalone JSON audit proof without database access |
| `--anchor-batch` | Generate and anchor a Merkle root batch over recent audit log entries |
| `--batch-size` | Batch size for Merkle audit root anchoring (default: 100) (default: `100`) |
| `--anchor` | Verify audit trail against an external anchor (format: <count>:<head_hash>) |
| `--print-anchor` | Print current audit anchor (<count>:<head_hash>) for external witnessing |
| `--sign-checkpoint` | Generate HMAC-SHA256 signed audit checkpoint using MDRAP_AUDIT_KEY |
| `-l`, `--limit` | Number of audit records to show (default: `20`) |

---

### `backtest`

**Description:** Historical backtesting engine with point-in-time event replay

**Aliases:** `bt`

**Usage:** `mdrap backtest [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `-s`, `--strategy` |  (default: `whale_momentum`) |
| `-i`, `--symbol` |  (default: `AAPL`) |
| `-c`, `--capital` |  (default: `100000.0`) |
| `--db` |  (default: `data/mdrap.db`) |

---

### `bars`

**Description:** Multi-timeframe OHLCV bar database and historical lookup

**Aliases:** `bardb`, `candles`

**Usage:** `mdrap bars [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `summary`) |

#### Options

| Option | Description |
| --- | --- |
| `-i`, `-s`, `--symbol` |  (default: `AAPL`) |
| `-t`, `--interval` |  (default: `1m`) |
| `-l`, `--limit` |  (default: `10`) |
| `--db` |  (default: `data/bars.db`) |

---

### `bbo`

**Description:** Query Synthetic Consolidated Best Bid & Offer (NBBO)

**Aliases:** `nbbo`

**Usage:** `mdrap bbo [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Instrument symbol (e.g. AAPL or 'all') |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |

---

### `benchmark`

**Description:** Run controlled benchmark and score quality detection

**Aliases:** `bench`

**Usage:** `mdrap benchmark [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` |  (default: `:memory:`) |
| `-e`, `--events` | Number of simulated events (default: `500000`) |
| `-s`, `--seed` | Random seed for reproducibility |
| `--duplicate-rate` |  |
| `--missing-rate` |  |
| `--out-of-order-rate` |  |
| `--malformed-rate` |  |
| `--price-anomaly-rate` |  |
| `--crossed-quote-rate` |  |
| `-m`, `--market` | Market profile (us, nse, xetra, tse, global) (default: `us`) |
| `-v`, `--version` | Pipeline version (default: `v1`) |
| `-f`, `--fastpath` | Enable Native C hot path accelerator (default: enabled) |
| `--no-fastpath` | Disable Native C accelerator and use pure Python |
| `-w`, `--warmup` | Warmup events (default: `5000`) |
| `-l`, `--label` | Benchmark label (default: `baseline`) |
| `-o`, `--out-dir` | Output directory for results (default: `benchmarks`) |
| `-p`, `--profile` | Profile with cProfile and dump stats |

---

### `chaos`

**Description:** Execute automated chaos & resilience drills (§15)

**Aliases:** `ch`

**Usage:** `mdrap chaos [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `drill` | Chaos drill type (default: `all`) |

#### Options

| Option | Description |
| --- | --- |
| `-e`, `--events` | Number of events (default: 50,000) (default: `50000`) |
| `-s`, `--seed` | Deterministic random seed (default: `42`) |
| `--kill-source` | Source to drop (default: `FEEDX`) |
| `--kill-start` | Drop begins after this many events (default: `0`) |
| `--kill-duration` | Number of events to drop (default: `500`) |

---

### `chart`

**Description:** Display visual in-terminal ASCII/Unicode candlestick chart

**Aliases:** `candle`

**Usage:** `mdrap chart [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to chart (e.g. AAPL, BTC/USD) (default: `AAPL`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-i`, `--interval` | Candlestick timeframe interval (e.g. 1s, 5s, 1m, 15m, 1h, default 5s) (default: `5s`) |
| `-w`, `--width` | Chart width in characters (default 56) (default: `56`) |
| `-H`, `--height` | Chart height in lines (default 10) (default: `10`) |
| `--duckdb` | Path to DuckDB database (default: `data/mdrap.duckdb`) |
| `--sim` | Simulate trade stream if no stored candles found |

---

### `columnar`

**Description:** Query high-performance DuckDB columnar time-series storage & analytics (Phase 3)

**Aliases:** `col`

**Usage:** `mdrap columnar [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Columnar operation (sync, ohlcv, vwap, spread, latency, profile, export, bench, sql, info) (default: `info`) |
| `target` | Target symbol, SQL query, or export output path |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--duckdb` | Path to DuckDB database file (default: data/mdrap.duckdb) (default: `data/mdrap.duckdb`) |
| `-i`, `--interval` | Resampling interval in seconds for OHLCV (default: 5.0) (default: `5.0`) |
| `-l`, `--limit` | Max rows to return (default: 20) (default: `20`) |
| `--bins` | Number of price bins for volume profile (default: 15) (default: `15`) |
| `-o`, `--output` | Parquet export output path |
| `--compression` | Parquet compression codec (default: zstd) (default: `zstd`) |
| `--full` | Force full SQLite table re-scan during sync instead of incremental CDC |

---

### `compare`

**Description:** Run V1 Pure Python vs V1 Native C on identical workloads and compare

**Aliases:** `comp`

**Usage:** `mdrap compare [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` |  (default: `:memory:`) |
| `-e`, `--events` | Number of simulated events (default: `100000`) |
| `-s`, `--seed` | Random seed for reproducibility |
| `--duplicate-rate` |  |
| `--missing-rate` |  |
| `--out-of-order-rate` |  |
| `--malformed-rate` |  |
| `--price-anomaly-rate` |  |
| `--crossed-quote-rate` |  |
| `-m`, `--market` | Market profile (us, nse, xetra, tse, global) (default: `us`) |
| `-w`, `--warmup` | Warmup events (default: `2000`) |

---

### `completion`

**Description:** Generate shell autocompletion script (bash, zsh, fish, powershell)

**Aliases:** `complete`

**Usage:** `mdrap completion [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `shell` | Target shell (default: bash) (default: `bash`) |

---

### `config`

**Description:** Inspect and query hierarchical mdrap.toml configuration

**Aliases:** `cfg`

**Usage:** `mdrap config [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `config_action` | Action (default: show) (default: `show`) |

#### Options

| Option | Description |
| --- | --- |
| `--venue` | Filter by venue code (e.g. binance, XNSE) |
| `--instrument`, `--symbol` | Filter by instrument symbol (e.g. BTCUSDT, AAPL) |
| `--instrument-class` | Filter by asset class (e.g. crypto, equity) |

---

### `core`

**Description:** Run standalone native C hot-path engine (T1 zero-lock tier)

**Aliases:** `fast-core`, `t1`

**Usage:** `mdrap core [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `-e`, `--events` | Number of simulated ticks (default: 100000) (default: `100000`) |
| `--shm` | Shared memory segment name (default: mdrap_feed) (default: `mdrap_feed`) |
| `--rate`, `-r` | Rate throttle in events/sec (0 = unconstrained) (default: `0`) |
| `--symbol` | Target symbol ticker (default: BTC/USD) (default: `BTC/USD`) |
| `--source` | Source identifier (default: FEEDX) (default: `FEEDX`) |
| `--build` | Recompile mdrap-core binary before executing |
| `--quiet`, `-q` | Suppress output |

---

### `corpact`

**Description:** Corporate actions processor: splits, dividends, ticker changes

**Aliases:** `dividends`, `splits`

**Usage:** `mdrap corpact [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `-i`, `-s`, `--symbol` |  (default: `AAPL`) |

---

### `daemon`

**Description:** Run headless streaming socket daemon service (§18)

**Aliases:** `d`

**Usage:** `mdrap daemon [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--host` | Listening host (default: 127.0.0.1) (default: `127.0.0.1`) |
| `-p`, `--port` | Listening port (default: 9876) (default: `9876`) |
| `--live` | Ingest real-time Binance & Coinbase market feeds |
| `-e`, `--events` | Event limit (0 for infinite continuous stream) (default: `0`) |
| `--speed` | Simulated events per second (default: `1000.0`) |
| `--token` | Pre-shared bearer authentication token for multi-user security |
| `--no-shm` | Disable zero-copy shared memory publisher |
| `--shm-name` | Shared memory segment name (default mdrap_feed) (default: `mdrap_feed`) |

---

### `dashboard`

**Description:** Launch real-time terminal visualizer dashboard

**Aliases:** `dash`

**Usage:** `mdrap dashboard [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--port` | TCP Gateway port (default: `9000`) |

---

### `deadletter`

**Description:** Inspect and replay uncommitted dead-letter transaction logs

**Usage:** `mdrap deadletter [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action: list pending files or replay into database (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--dir` | Custom dead-letter spill directory path |

---

### `demo`

**Description:** Execute bundled 50k-event run and open live desk navigator

**Aliases:** `dm`

**Usage:** `mdrap demo [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |

---

### `depth`

**Description:** Show Consolidated Level-2 Multi-Venue Market Depth Ladder

**Aliases:** `l2`

**Usage:** `mdrap depth [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to inspect (e.g. BTC/USD) (default: `BTC/USD`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-l`, `--limit` | Number of depth levels per side (default 10) (default: `10`) |

---

### `desk`

**Description:** Launch interactive keyboard-first modal desk navigator (Vim/Excel ergonomics)

**Aliases:** `nav`

**Usage:** `mdrap desk [OPTIONS]`

---

### `doctor`

**Description:** Inspect environment, compiler, engine tier, WAL status, and run 10k smoke check

**Aliases:** `doc`

**Usage:** `mdrap doctor [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |

---

### `edgar`

**Description:** SEC EDGAR Alternative Data: 8-K material events, Form 4 insiders, GAAP facts

**Aliases:** `filings`

**Usage:** `mdrap edgar [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action to perform (default: events) (default: `events`) |
| `ticker` | Company ticker symbol (default: AAPL) (default: `AAPL`) |

#### Options

| Option | Description |
| --- | --- |
| `-t`, `--type` | Filter by form type (e.g., 10-K, 10-Q, 8-K, 4) |
| `-l`, `--limit` | Maximum number of items to display (default: 15) (default: `15`) |
| `-m`, `--metric` | GAAP metric name for facts (default: Revenues) (default: `Revenues`) |
| `-f`, `--fresh` | Bypass local cache and force fresh SEC pull |
| `-o`, `--open` | Open the latest filing or document directly in default web browser |

---

### `export`

**Description:** Export market microstructure data to Excel (.xlsx) or CSV

**Aliases:** `exp`

**Usage:** `mdrap export [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to export (default: AAPL) (default: `AAPL`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-o`, `--output` | Custom output file or directory path |
| `--outdir` | Directory for exported reports (default: data/reports) (default: `data/reports`) |
| `--csv` | Export as structured CSV package instead of Excel (.xlsx) |
| `--format` | Export format: excel, csv, parquet, json |
| `--table` | Database table to export (default: canonical_events) (default: `canonical_events`) |
| `--open` | Automatically launch generated workbook in Excel (Windows only) |

---

### `failover`

**Description:** Active-Passive cluster failover coordinator

**Aliases:** `fo`

**Usage:** `mdrap failover [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Failover command action (default: `status`) |

#### Options

| Option | Description |
| --- | --- |
| `--node-id` | Cluster node ID (default: `node-local`) |
| `--cluster` | Cluster name (default: `mdrap-cluster`) |
| `--reason` | Transition reason (default: `Manual operator request`) |
| `--seq` | Heartbeat sequence number (default: `0`) |

---

### `features`

**Description:** ML feature store: technical indicators and microstructure metrics

**Aliases:** `feat`

**Usage:** `mdrap features [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `-i`, `-s`, `--symbol` |  (default: `AAPL`) |

---

### `feed`

**Description:** Inspect, benchmark, and test streaming feeds (Polygon, Databento, Crypto WS)

**Aliases:** `feeds`

**Usage:** `mdrap feed [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--source` | Streaming feed source (default: `databento`) |
| `--symbols` | Comma-separated symbols to stream (default: `AAPL,MSFT,NVDA`) |
| `-c`, `--count` | Number of packets to ingest (default: `50`) |
| `--mock` | Use high-fidelity wire-format mock stream |
| `--key` | API key for feed provider |

---

### `flow`

**Description:** Track Institutional Order Flow, Lee-Ready Aggressor Side, CVD & MPID Net Deltas

**Aliases:** `cvd`

**Usage:** `mdrap flow [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Instrument symbol (default: AAPL) (default: `AAPL`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-c`, `--count` | Number of trades to analyze (default: 500) (default: `500`) |
| `-s`, `--seed` | Deterministic random seed (default: `42`) |
| `--whales` | Display only whale blocks and institutional prints |
| `--export` | Export 3-tab Order Flow & CVD Excel report (.xlsx) |
| `--open` | Open exported report in Microsoft Excel (Windows only) |

---

### `gateway`

**Description:** Launch AsyncIO TCP Gateway for external clients

**Aliases:** `gw`

**Usage:** `mdrap gateway [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--host` | Listening host (default: 127.0.0.1) (default: `127.0.0.1`) |
| `-p`, `--port` | Listening port (default: 9000) (default: `9000`) |

---

### `historical`

**Description:** CHD historical data: discover, download and ingest

**Aliases:** `chd`, `history`

**Usage:** `mdrap historical [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `historical_action` |  |

---

### `itch`

**Description:** NASDAQ TotalView-ITCH 5.0 Binary Feed Engine & Global Benchmark

**Aliases:** `totalview`

**Usage:** `mdrap itch [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action to perform (default: bench) (default: `bench`) |
| `file` | Path to .itch or .itch.gz file (for parse) |

#### Options

| Option | Description |
| --- | --- |
| `-e`, `--events` | Number of messages (for bench/generate, default: 1,000,000) (default: `1000000`) |
| `-o`, `--output` | Output file path (for generate) (default: `data/sample.itch`) |
| `-l`, `--limit` | Number of records to preview (for parse) (default: `50`) |

---

### `keys`

**Description:** Manage client API keys and authentication tokens

**Usage:** `mdrap keys [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action to perform (default: list) (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--client-id` | Client identifier name (for create) (default: `Custom_Client`) |
| `--role` | Role assignment: VIEWER, OPERATOR, or ADMIN (for create) (default: `VIEWER`) |
| `--rate` | Custom rate limit eps |
| `--token` | API key token (for revoke or rotate) |
| `--prefix` | API key prefix (for revoke) |
| `--grace` | Rotation grace period in seconds (default: 3600) (default: `3600.0`) |

---

### `lake`

**Description:** Manage partitioned historical market data store

**Aliases:** `partitions`, `store-hist`

**Usage:** `mdrap lake [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Historical store action (default: `catalog`) |

#### Options

| Option | Description |
| --- | --- |
| `--base-dir` | Historical base storage directory (default: `data/historical`) |
| `--from-db` | Source SQLite DB for partitioning (default: `data/mdrap.db`) |
| `--format` | Partition storage format (default: `auto`) |
| `--symbol` | Filter by symbol |
| `--limit` | Query row limit (default: `20`) |
| `--days` | Retention max age in days (default: `30`) |
| `--dry-run` | Retention dry run without deletion |

---

### `live`

**Description:** Stream live market ticks with in-place updating table & candlestick chart

**Aliases:** `stream`

**Usage:** `mdrap live [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to stream (e.g. BTC/USD, AAPL, or 'all') (default: `BTC/USD`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-l`, `--limit` | Number of ticks to stream (default 20, 0 for continuous) (default: `20`) |
| `--fast` | High-speed streaming mode (10ms poll interval, high-frequency terminal updates) |
| `--poll-ms` | Polling interval in milliseconds (e.g. --poll-ms 10 for 10ms) |
| `--ws` | Stream using true real-time WebSockets (<1ms push) instead of HTTP polling |
| `--sim` | Use realistic multi-venue simulator stream instead of public internet API |
| `--feed` | Streaming feed source provider |
| `--mock-feed` | Run provider in high-fidelity wire-format mock generator mode |
| `--polygon-key` | Polygon.io API key (or set POLYGON_API_KEY env var) |
| `--databento-key` | Databento API key (or set DATABENTO_API_KEY env var) |
| `--dbn-file` | Path to historical .dbn binary file to stream |

---

### `loadtest`

**Description:** Sweep increasing event volumes and report trend

**Aliases:** `load`

**Usage:** `mdrap loadtest [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--levels` | Comma-separated event counts (default: `10000,50000,100000,250000,500000`) |
| `-s`, `--seed` | Deterministic random seed (default: `42`) |
| `-o`, `--out-dir` |  (default: `benchmarks`) |

---

### `markets`

**Description:** Global financial exchange directory, market clocks, and microstructure

**Aliases:** `venues`, `world`

**Usage:** `mdrap markets [OPTIONS]`

---

### `mbo`

**Description:** Inspect Level-3 Market-By-Order (MBO) FIFO queue ranks and L2 book projection (§18, §26)

**Aliases:** `l3`

**Usage:** `mdrap mbo [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to inspect (default: AAPL) (default: `AAPL`) |

#### Options

| Option | Description |
| --- | --- |
| `-l`, `--limit` | Depth levels to display (default: 5) (default: `5`) |

---

### `news`

**Description:** Financial news aggregation, sentiment analysis, and entity extraction

**Aliases:** `sentiment`

**Usage:** `mdrap news [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `latest`) |
| `text` | Headline text to analyze (when action is analyze) |

#### Options

| Option | Description |
| --- | --- |
| `-s`, `--symbol` | Stock or crypto ticker symbol (e.g. NVDA, AAPL, BTC) |
| `-l`, `--limit` | Maximum number of headlines to display (default: `10`) |
| `--feed` | Custom RSS feed URL or source identifier |
| `-j`, `--json` | Output results in JSON format |

---

### `options`

**Description:** Options pricing models, Greeks chain, and implied volatility solver

**Aliases:** `greeks`, `opt`

**Usage:** `mdrap options [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `price`) |

#### Options

| Option | Description |
| --- | --- |
| `-s`, `--spot` |  (default: `100.0`) |
| `-k`, `--strike` |  (default: `100.0`) |
| `-e`, `--expiry` |  (default: `30.0`) |
| `-v`, `--vol` |  (default: `0.25`) |
| `-r`, `--rate` |  (default: `0.05`) |
| `-t`, `--type` |  (default: `call`) |

---

### `plugins`

**Description:** List installed plugins and extension entry points

**Usage:** `mdrap plugins [OPTIONS]`

---

### `portfolio`

**Description:** Portfolio tracker with P&L attribution and benchmark comparison

**Aliases:** `port`

**Usage:** `mdrap portfolio [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `summary`) |

#### Options

| Option | Description |
| --- | --- |
| `-c`, `--capital` |  (default: `100000.0`) |
| `--currency` | Base reporting currency (e.g. USD, INR, EUR, JPY) (default: `USD`) |
| `--db` |  (default: `data/portfolio.db`) |

---

### `query`

**Description:** Inspect stored data: health, latest, lineage, quarantine

**Aliases:** `q`

**Usage:** `mdrap query [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action: health, latest, lineage, quarantine, counts |
| `target` | Target symbol, event ID, or sample count |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--latest` | Latest event for instrument |
| `-l`, `--limit` | Row limit (default: `1`) |
| `--lineage` | Lineage for event ID |
| `--health` | Feed health summary |
| `--quarantine` | Quarantine sample |

---

### `replay`

**Description:** Replay archived raw events through the pipeline

**Aliases:** `rep`

**Usage:** `mdrap replay [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap_replay.db`) |
| `--base-dir` | Archive directory (default: `data/raw_archive`) |
| `-d`, `--date` | Replay only a specific date (YYYY-MM-DD) |
| `-s`, `--source` | Replay only a specific source |
| `--speed` | Replay speed factor (1.0=realtime, 10.0=10x, 0=unthrottled) |
| `--symbol` | Filter replay by instrument symbol |
| `--limit` | Limit number of replayed events |
| `--from-sqlite` | Load replay events from SQLite database path |
| `--from-journal` | Load replay events from binary journal (.dbn) path |

---

### `report`

**Description:** Generate institutional fund regulatory compliance reports (SEC 13F, MiFID II RTS 28)

**Aliases:** `reg`

**Usage:** `mdrap report [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `report_type` | Report type: '13f' (SEC Form 13F Holdings) or 'rts28' (MiFID II Execution Venues) (default: `13f`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--symbol` | Target symbol for venue analysis (default: AAPL) (default: `AAPL`) |
| `-s`, `--seed` | Deterministic random seed (default: `42`) |

---

### `retention`

**Description:** Run storage retention compaction and disk reclamation

**Aliases:** `prune`

**Usage:** `mdrap retention [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--days` | Retention window for canonical events in days (default: 30) (default: `30`) |
| `--quarantine-days` | Retention window for quarantine records in days (default: 90) (default: `90`) |
| `--vacuum` | Execute full SQLite VACUUM to reclaim filesystem disk space |

---

### `risk`

**Description:** Portfolio risk management, VaR, CVaR, and correlation analysis

**Aliases:** `cvar`, `var`

**Usage:** `mdrap risk [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `-c`, `--capital` |  (default: `100000.0`) |
| `-w`, `--window` |  (default: `252`) |
| `-p`, `--confidence` |  (default: `0.95`) |
| `--db` |  (default: `data/mdrap.db`) |

---

### `run`

**Description:** Run the pipeline against the simulator (optionally with live dashboard)

**Aliases:** `r`

**Usage:** `mdrap run [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-e`, `--events` | Number of simulated events (default: `50000`) |
| `-s`, `--seed` | Random seed for reproducibility |
| `--duplicate-rate` |  |
| `--missing-rate` |  |
| `--out-of-order-rate` |  |
| `--malformed-rate` |  |
| `--price-anomaly-rate` |  |
| `--crossed-quote-rate` |  |
| `-m`, `--market` | Market profile (us, nse, xetra, tse, global) (default: `us`) |
| `-v`, `--version` | Pipeline version (v1: sync, v2: streaming) (default: `v1`) |
| `-f`, `--fastpath` | Enable Native C hot path accelerator (default: enabled) |
| `--no-fastpath` | Disable Native C accelerator and use pure Python |
| `-a`, `--archive` | Enable immutable raw event archiving to data/raw_archive/ |
| `--no-analytics` | Disable V3 analytics aggregation |
| `-d`, `--dashboard` | Show live rich terminal dashboard |
| `--strict-sync` | Exit 1 if secondary DuckDB sync fails (prevents silent store divergence in automation) |
| `--no-sync` | Skip automatic DuckDB columnar store sync at run completion |

---

### `schedule`

**Description:** Scheduled tasks and automated EOD reports

**Aliases:** `cron`, `sched`

**Usage:** `mdrap schedule [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `list`) |

---

### `sdk-demo`

**Description:** Run Quant-Ready Python SDK Client Demo

**Aliases:** `sdk`

**Usage:** `mdrap sdk-demo [OPTIONS]`

---

### `security`

**Description:** Display platform security posture, HMAC verification, RBAC, and rate limiting status

**Aliases:** `sec`

**Usage:** `mdrap security [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |

---

### `serve`

**Description:** Start production REST API and WebSocket event streaming server

**Aliases:** `api`

**Usage:** `mdrap serve [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--host` | Bind host (default: 0.0.0.0) (default: `0.0.0.0`) |
| `--port` | Bind port (default: 8000) (default: `8000`) |
| `--reload` | Enable auto-reload for development |

---

### `shell`

**Description:** Launch low-latency interactive slash-command shell

**Aliases:** `sh`

**Usage:** `mdrap shell [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |

---

### `simulate`

**Description:** Simulate concurrent multi-device normal vs fast-paced user workloads (§26)

**Aliases:** `sim`

**Usage:** `mdrap simulate [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--scale` | Simulation scale tier (default: desk) (default: `desk`) |
| `-t`, `--duration` | Simulation duration in seconds (default: 5.0) (default: `5.0`) |
| `--normal` | Number of normal user devices (for custom scale) (default: `3`) |
| `--fast` | Number of fast-paced bot devices (for custom scale) (default: `3`) |
| `--monitor` | Number of DevOps monitor devices (for custom scale) (default: `0`) |
| `--mode` | Worker concurrency mode (default: thread) (default: `thread`) |
| `--port` | Streaming daemon TCP port (default: 19880) (default: `19880`) |
| `--prom-port` | Prometheus HTTP port (default: 19110) (default: `19110`) |
| `--eps` | Simulated feed tick generation rate (default: 3000.0) (default: `3000.0`) |
| `--duckdb` | Path to DuckDB database (default: `data/mdrap.duckdb`) |
| `-o`, `--report` | Save JSON performance report to file |

---

### `status`

**Description:** Show comprehensive platform status overview

**Aliases:** `s`

**Usage:** `mdrap status [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |

---

### `strategy`

**Description:** Institutional Algorithmic Strategy Engine & Paper EMS (§26)

**Aliases:** `strat`

**Usage:** `mdrap strategy [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action to perform (default: list) (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-s`, `--strategy` | Strategy name (default: `whale_momentum`) |
| `-i`, `--symbol` | Instrument symbol (default: AAPL) (default: `AAPL`) |
| `-e`, `--events` | Event count for paper simulation (default: 1000) (default: `1000`) |
| `-b`, `--book`, `--show-book` | Display Level-2 Order Book depth ladder at run completion |
| `-x`, `--executions`, `--trades` | Display detailed strategy execution ledger with arrival prices and slippage |
| `--export` | Export strategy execution log and order book history to JSON or CSV |

---

### `stress`

**Description:** Run multi-directional stress tests and 1M to 1B scale analysis

**Aliases:** `str`

**Usage:** `mdrap stress [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--module` | Target module to stress (default: `all`) |
| `-e`, `--events` | Number of stress events (default 25,000) (default: `25000`) |

---

### `sub`

**Description:** Subscribe to daemon stream and output ticks or depth to stdout

**Aliases:** `subscribe`

**Usage:** `mdrap sub [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to stream (e.g. BTC/USD, AAPL, or ALL) (default: `ALL`) |

#### Options

| Option | Description |
| --- | --- |
| `--host` | Listening host (default: 127.0.0.1) (default: `127.0.0.1`) |
| `-p`, `--port` | Listening port (default: 9876) (default: `9876`) |
| `-l`, `--limit` | Limit number of ticks (0 for continuous) (default: `0`) |
| `--l2` | Subscribe to Consolidated Level-2 Depth ladders |
| `--vwap` | Subscribe to real-time institutional VWAP curves |
| `--shm` | Read directly from zero-copy shared memory buffer (<1µs latency) |
| `--shm-name` | Shared memory segment name (default mdrap_feed) (default: `mdrap_feed`) |
| `--binary` | Stream using fixed-width binary protocol (MDRAP-BIN V1, ~75% smaller, <2µs) |
| `-j` | Output raw JSON for piping into jq or trading bots |
| `--token` | Pre-shared bearer authentication token |

---

### `tca`

**Description:** Run Institutional Best Execution & TCA Slippage Engine with Merkle Proofs

**Aliases:** `bestex`

**Usage:** `mdrap tca [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Instrument symbol (default: AAPL) (default: `AAPL`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `-c`, `--count` | Number of demo execution records to generate (default: 50) (default: `50`) |
| `-f`, `--file` | Path to execution records CSV file |
| `-s`, `--seed` | Deterministic random seed (default: `42`) |
| `--demo` | Run with realistic multi-broker demo dataset |
| `--benchmark` | Benchmark price for slippage calculation (default: `ARRIVAL_PRICE`) |
| `--export` | Export 3-tab audit-grade Excel TCA report (.xlsx) |
| `--open` | Open exported report in Microsoft Excel (Windows only) |

---

### `test-all`

**Description:** Run all CLI tests, benchmarks, queries, and validations in one place

**Aliases:** `t`

**Usage:** `mdrap test-all [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap_test.db`) |
| `--duckdb` | Path to DuckDB database (default: `data/mdrap_test.duckdb`) |
| `-s`, `--seed` |  (default: `42`) |

---

### `throughput`

**Description:** Benchmark 500,000 to 1,000,000+ events/sec on vectorized Native C SBE stream (§26)

**Aliases:** `tp`

**Usage:** `mdrap throughput [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `-e`, `--events` | Number of events to benchmark (e.g. 500000 or 1000000, default: 1,000,000) (default: `1000000`) |
| `--anomalies` | Anomaly injection rate (default: 0.01 = 1%%) (default: `0.01`) |
| `--compare` | Display architectural progression comparison table |

---

### `top`

**Description:** Launch dynamic full-screen terminal service cockpit

**Aliases:** `mon`

**Usage:** `mdrap top [OPTIONS]`

#### Options

| Option | Description |
| --- | --- |
| `--host` | Listening host (default: 127.0.0.1) (default: `127.0.0.1`) |
| `-p`, `--port` | Listening port (default: 9876) (default: `9876`) |
| `--token` | Pre-shared bearer authentication token |

---

### `version`

**Description:** Show MDRAP version

**Aliases:** `v`

**Usage:** `mdrap version [OPTIONS]`

---

### `vessel`

**Description:** Maritime Tanker & Cargo Tracking: Crude oil, LNG, bulk, and container tracking

**Aliases:** `ais`

**Usage:** `mdrap vessel [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action to perform (default: list) (default: `list`) |
| `identifier` | Vessel IMO, MMSI, or Name to track |

#### Options

| Option | Description |
| --- | --- |
| `-t`, `--type` | Filter by vessel type (e.g. tanker, lng, bulk, container) |
| `-c`, `--company` | Filter by operating or chartering company (e.g. Frontline, Shell, Aramco, Maersk) |
| `-k`, `--chokepoint` | Filter by nearest chokepoint (e.g. hormuz, suez, malacca) |
| `-s`, `--status` | Filter by cargo load status (laden, ballast) |
| `-l`, `--limit` | Maximum number of vessels to display (default: 25) (default: `25`) |

---

### `vwap`

**Description:** Compute multi-venue real-time VWAP execution & slippage curves

**Aliases:** `curve`

**Usage:** `mdrap vwap [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `symbol` | Symbol to inspect (e.g. BTC/USD) (default: `BTC/USD`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--sizes` | Order sizing tranches (default: 1 5 10 25 50) (default: `[1.0, 5.0, 10.0, 25.0, 50.0]`) |

---

### `wal`

**Description:** Verify, inspect, or salvage IngestLog Write-Ahead Log segments

**Aliases:** `log`

**Usage:** `mdrap wal [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action: verify (integrity check) or salvage (recover valid frames) (default: `verify`) |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--wal-path` | Custom WAL directory path (defaults to <db>.wal) |
| `--no-backup` | Do not create backup files during salvage |

---

### `watchdog`

**Description:** Show source health status and watchdog alerts

**Aliases:** `w`

**Usage:** `mdrap watchdog [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` | Action: status or alerts |
| `target` | Alert limit count |

#### Options

| Option | Description |
| --- | --- |
| `--db` | Database path (default: `data/mdrap.db`) |
| `--status` | Show current source health status |
| `-a`, `--alerts` | Show recent watchdog alerts |
| `-l`, `--limit` | Alert count limit (default: `10`) |

---

### `watchlist`

**Description:** Named watchlist management

**Aliases:** `wl`

**Usage:** `mdrap watchlist [OPTIONS]`

#### Positional Arguments

| Argument | Description |
| --- | --- |
| `action` |  (default: `list`) |

#### Options

| Option | Description |
| --- | --- |
| `-n`, `--name` |  (default: `Tech`) |
| `-s`, `--symbols` |  (default: `['AAPL', 'MSFT']`) |
| `--db` |  (default: `data/portfolio.db`) |

---
