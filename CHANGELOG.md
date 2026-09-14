# Changelog

## [1.2.0] - 2026-09-14
**Modal Keyboard Navigator Desk, Platform-Wide Silent Error Elimination & Over-Engineering Cleanup**

### Added
- **Keyboard-First Modal Navigator Desk (`mdrap desk`, `src/navigator.py`)**:
  - Full-screen interactive terminal workspace inspired by Bloomberg Terminal and Vim.
  - 5 integrated multi-domain views: Markets (`1`), Fleet (`2`), Depth (`3`), EDGAR (`4`), and Portfolio (`5`).
  - Virtual data grid with viewport scrolling, persistent row selection, and dynamic column sorting (`s`).
  - Dedicated instant filter mode (`/`) with live multi-column matching and `[Esc]` cancellation.
  - Strict modal safety boundary: 95% single-keystroke pure reads vs. 5% armed mutating tickets (`b` BUY, `S` SELL) gated by an explicit confirmation ticket requiring `[Enter]` or `[Esc]`.
- **DuckDB Divergence Transparency & Strict Sync Mode (`src/cli.py`)**:
  - `mdrap run` now explicitly detects and surfaces automatic DuckDB synchronization errors to `sys.stderr` instead of silently swallowing failures.
  - Structured run metrics output now includes `"diverged": true` when SQLite is updated but DuckDB fails to sync.
  - Added `--strict-sync` flag which immediately exits with code 1 upon sync failure for CI/CD and automation reliability.
  - Added `--no-sync` flag to completely bypass DuckDB synchronization when operating strictly on SQLite.
- **Feed Error Quarantine Pipeline (`src/ws_feed.py`, `src/polygon_feed.py`)**:
  - Malformed or corrupt wire frames now return a `RawEvent` marked with `is_malformed=True` instead of returning `None`.
  - Events are routed to `normalize()`, triggering `SchemaError` and direct ingestion into the quarantine database with reason `SCHEMA_VIOLATION` and full lineage per Principle 3 ("Never silently discard bad data").

### Fixed
- **API Key Persistence Desynchronization (`src/security.py`)**:
  - `load_api_keys()` logs warnings to `sys.stderr` on database read failure.
  - `register_api_key()` evicts the generated token from in-memory cache and raises `RuntimeError` if SQLite persistence fails.
  - `revoke_api_key()` raises `RuntimeError` on database write failure, preventing revoked keys from reviving after daemon restart.
- **Daemon Shared Memory (SHM) Telemetry & Error Tracking (`src/service.py`)**:
  - SHM initialization failure surfaces an operator warning to `sys.stderr`.
  - Real-time `write_tick` and `write_depth` exception handlers now count failures (`_shm_errors`) and log diagnostic warnings.
  - Daemon telemetry `stats()` now includes `"shm_enabled"` and `"shm_errors"`.
- **Central Configuration Parse Failure Warnings (`src/config.py`, `src/quality.py`)**:
  - `load_config()` prints warnings to `sys.stderr` when a candidate configuration file exists but fails parsing, rather than silently ignoring syntax errors.
  - `QualityEngine.__init__` surfaces configuration loading errors.
- **News Feed Fallback Transparency (`src/trading_cli.py`)**:
  - `cmd_news` captures network failures and displays a clear notice when falling back to the curated baseline headlines.

### Removed
- **Ponytail Over-Engineering Cleanup**:
  - `src/strategy_sdk.py`: Deleted dead speculative async `LiveStrategyRunner` (-39 lines) and unused `OrderBookLevel` (-9 lines).
  - `src/shm.py`: Deleted in-module `benchmark_shm_latency` microbenchmark (-54 lines).
  - `src/terminal_display.py`: Deleted redundant `run_watchlist_stream` pass-through method (-19 lines).
  - `src/bbo.py`: Deleted uncalled `prune_stale` cache eviction method (-16 lines).
  - `src/storage.py`: Deleted uncalled `write_alert_batch` method (-9 lines).
  - `src/depth.py`: Deleted uncalled `all_ladders` method (-3 lines).
  - `src/reconciliation.py`: Deleted dead `cumulative_error_rate` property (-4 lines).
  - `src/client.py`: Deleted dead `is_vwap` property (-4 lines).
  - `src/options.py`: Replaced hand-rolled `_norm_cdf` and `_norm_pdf` wrappers with direct stdlib aliases `_STD_NORM.cdf` and `_STD_NORM.pdf` (-8 lines).

### Verified
- **Test Suite**: 650 unit, integration, and quantitative tests passing (`pytest tests/ -q` 100% green in 84.35s).
- Added [`tests/test_error_surfacing.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_error_surfacing.py) and [`tests/test_navigator.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_navigator.py).

## [1.1.0] - 2026-09-14
**Native C Hot Path Default Enablement, Multi-Market Infrastructure & Maritime Intelligence Restoration**

### Added
- **Native C Hot Path Enabled by Default (`src/fastpath.py`, `src/pipeline.py`, `src/cli.py`)**:
  - `Pipeline` defaults to `FastQualityEngine` when compiled C fastpath (`fastpath.dll` / `fastpath.so`) is available.
  - Added `--no-fastpath` flag to CLI (`mdrap run`, `mdrap benchmark`) for pure-Python execution.
  - Automatic zero-overhead fallback to pure Python when `MDRAP_DISABLE_FASTPATH=1` is set.
- **Global Multi-Market Infrastructure (`src/venues.py`, `src/symbology.py`, `src/fx.py`)**:
  - ISO 10383 venue models for Indian (`XNSE`, `XBOM`), German (`XETR`, `XEUR`), Japanese (`XTKS`, `XOSE`), UK (`XLON`), and US (`XNAS`, `XNYS`) markets.
  - Global trading desk clock (`mdrap markets` / `mdrap desk`).
  - Triangular FXMatrix engine for multi-currency portfolio valuation (`mdrap portfolio --currency INR/EUR/JPY/GBP`).
  - Universal symbology resolver for exchange suffixes, Bloomberg tickers, Reuters RICs, and ISINs.
  - Venue-aware quality rules: `CIRCUIT_FILTER_BREACH`, `VOLATILITY_INTERRUPTION`, and `SPECIAL_QUOTE_INDICATION`.
- **Global Maritime Tanker & Cargo Tracking Alternative Data Engine (`src/vessel.py`)**:
  - Commercial fleet tracking for crude oil tankers (VLCC/ULCC), LNG, dry bulk, and container ships.
  - Native C vectorized geodesic fastpath bindings (`fastpath_haversine_nm`, `fastpath_batch_fleet_geofence`).
  - Geopolitical maritime chokepoints monitoring (Hormuz, Malacca, Suez, Bab-el-Mandeb, Panama).
  - CLI commands: `mdrap vessel list`, `mdrap vessel track`, `mdrap vessel chokepoints`, `mdrap vessel commodities`.

### Removed
- **Speculative & Redundant Architecture Purge**:
  - `src/excel_bridge.py`: Deleted HTTP Excel BDP formula server and VBA generator.
  - `src/pipeline_v2.py`: Deleted decoupled multi-threaded streaming pipeline to maintain pure V1 synchronous baseline.
  - `src/sharded_pipeline.py`: Deleted multi-process sharded pipeline engine.
  - `src/async_storage.py`: Deleted background thread async storage sink.
  - `src/feed_workers.py`: Deleted multi-threaded background feed ingestion workers.
  - `src/prometheus.py`: Deleted embedded HTTP daemon for Prometheus metrics scraping.
  - `src/broker.py`: Deleted unused abstract EventBroker interface and Kafka adapter.
  - `src/spsc_ring.py`: Deleted redundant ring buffer duplicating `src/shm.py`.
  - `src/chd_cli.py`: Deleted redundant wrapper duplicating `src/chd.py`.
  - `src/sdk/`: Consolidated SDK directly into `src/client.py` and `src/strategy_sdk.py`.
- **Artificial Enterprise Paywalls & Trial License Gates**:
  - Removed commercial tier checks and mock upgrade nag screens in `src/security.py` and `src/service.py`. All institutional capabilities are unrestricted.

### Fixed
- **Watchlist & Portfolio Ephemeral SQLite Connection Bug (`src/portfolio.py`)**:
  - Fixed `WatchlistManager` and `PortfolioTracker` opening temporary in-memory connections on every operation (`sqlite3.connect(self.db_path)`), which wiped tables when `:memory:` was passed. Persisted connection `self._conn` across instance lifetime.
- **CLI Typo Auto-Correct False Positive (`src/cli.py`)**:
  - Fixed false-positive notice where valid canonical subcommands (`mdrap version`, etc.) triggered redundant auto-correct notices.

### Verified
- **Test Suite**:
  - 629 unit, integration, and quantitative tests passing (`python -m pytest tests/ -q` 100% green in ~90s).
- **Latency & Throughput Verification**:
  - Multi-market latency benchmark verified (~18,000–22,000 EPS e2e, fastpath C quality kernel 28.0 ns/e).

## [1.0.6] - 2026-09-13
**SEC EDGAR Untruncated Link Fix, OSC 8 Terminal Hyperlinks & Browser Launch (`--open`)**

### Fixed
- **SEC EDGAR URL Truncation 404 Resolution (`src/cli.py`)**:
  - Fixed terminal table cell width truncation where long SEC EDGAR URLs (e.g. `https://www.sec.gov/Archives/edgar/data/320193/...`) were truncated by Rich with an ellipsis character (`.../data/3201…`), causing terminal click handlers and copy-paste to navigate to non-existent truncated endpoints (`.../data/3201`) resulting in SEC HTTP 404 errors.
  - Replaced plain text URLs in table cells with Rich OSC 8 terminal hyperlinks: `[link=URL]Open Document[/link]`. In modern terminal emulators (Windows Terminal, VS Code, iTerm2), clicking "Open Document" opens the full, untruncated SEC filing URL directly in the browser.
  - Added dedicated, untruncated **Direct SEC Document Links** footer printed below the table, ensuring that even legacy terminals without OSC 8 support can copy and click clean, un-split URLs.

### Added
- **Direct Browser Launch (`--open` / `-o`)**:
  - Added `-o` / `--open` flag to `mdrap edgar` (`p_edgar.add_argument("-o", "--open")`). Users can now launch the top filing, Form 4 insider report, or 8-K event directly in their default web browser with `mdrap edgar filings NVDA -o` or `mdrap edgar profile AAPL -o`.
- **Test Coverage**:
  - Added `test_edgar_open_argument` in `tests/test_cli.py` to ensure `--open` flag parsing and defaults are rigorously validated.

## [1.0.5] - 2026-09-13
**Financial News & Sentiment Pipeline Expansion (`news latest -s <TICKER>`) & Ticker Extraction**

### Added
- **News Command Full Feature Set (`src/trading_cli.py`, `src/cli.py`)**:
  - `news latest -s <TICKER>`: Live financial headline aggregation and sentiment scoring.
  - `news summary -s <TICKER>`: Multi-article sentiment consensus summary (% Bullish, % Bearish, % Neutral, Average Score).
  - `news <TICKER>`: Direct ticker query shorthand (e.g. `mdrap news NVDA`).
  - `news analyze "<HEADLINE>"`: Detailed NLP sentiment breakdown with urgency and keyword tags.
- **Company Name Entity Resolution (`src/news.py`)**:
  - `TickerExtractor`: Added `COMPANY_NAME_MAP` to resolve common company names (e.g. "Nvidia" $\rightarrow$ `NVDA`, "Apple" $\rightarrow$ `AAPL`, "Microsoft" $\rightarrow$ `MSFT`, "Tesla" $\rightarrow$ `TSLA`, "Bitcoin" $\rightarrow$ `BTC`) to their canonical exchange tickers.
- **CLI & Shell Auto-Correction for News**:
  - Added `news` and `sentiment` dispatch heuristics in `cmd_shell` and `main()` supporting auto-correction and positional ticker arguments.

## [1.0.4] - 2026-09-13
**CLI Fuzzy Typo Auto-Correction, Scoped Error Reporting & Real-World Command Ergonomics**

### Added
- **Intelligent Fuzzy Typo Auto-Correction (`src/cli.py`)**:
  - Subcommand action auto-correction via `difflib.get_close_matches` (e.g. `edgar fillings NVDA` auto-resolves to `filings` with notice).
  - Primary command auto-correction in both the CLI entry point and interactive quant shell (e.g. `mdrap edgr` $\rightarrow$ `edgar`, `mdrap choas` $\rightarrow$ `chaos`, `mdrap benh` $\rightarrow$ `bench`).
  - Action validation and suggestion support across SEC EDGAR, Vessel Tracking, and Derivatives options commands.
- **Scoped CLI Error Reporting (`MDRAPArgumentParser`)**:
  - Subclassed `argparse.ArgumentParser` to intercept subcommand errors and suppress the 50-line root usage wall.
  - Generates command-specific syntax guides and "Did you mean?" suggestions on invalid choices.
  - Automatically isolates subparser execution contexts so errors report the active subcommand rather than the root CLI.
- **Ticker-First Syntax Disambiguation**:
  - Enhanced tokenizer logic to prioritize fuzzy command matches over unknown ticker assumptions, preventing misspelled commands from being mistakenly evaluated as stock tickers.
- **Documentation & User Guide Updates**:
  - Added dedicated section on CLI error resilience and typo handling in `docs/USER_GUIDE.md`.
  - Added "Real-World Live Commands Cheat Sheet" in `README.md` covering SEC EDGAR, live L2 depth, options pricing, order flow, chaos drills, and benchmarks.

## [1.0.3] - 2026-09-13
**Native C Hot-Path Expansion (Phase 13), Automated Git/Pip Packaging & Codebase Leaning**

### Added
- **Phase 13 Native C Hot-Path Accelerator Expansion (`src/fastpath.c`, `src/fastpath.dll`)**:
  - **American Options Pricing**: Cox-Ross-Rubinstein binomial tree in native C (`fastpath_binomial_price` achieving **0.21 ms vs 13.62 ms in pure Python, 64.1x faster**).
  - **Quantitative Feature Store Kernels**:
    - Bollinger Bands (`fastpath_calc_bollinger` achieving **1.04 ms vs 53.61 ms, 51.6x faster**).
    - Wilder-smoothed RSI (`fastpath_calc_rsi` achieving **3.01 ms vs 6.62 ms, 2.2x faster**).
    - Average True Range (`fastpath_calc_atr`).
  - **Portfolio Risk Kernels**:
    - High-throughput Monte Carlo VaR with 64-bit XorShift128+ PRNG and Box-Muller Gaussian transforms (**3.44 ms vs 8.07 ms, 2.3x faster**).
  - **FIX Protocol Checksum**: Vectorized byte sum modulo 256 (**2.25 µs vs 6.92 µs, 3.1x faster**).
- **Automated Git & Pip Native C Enablement (`v1.0.3`)**:
  - **Multi-Compiler Build Engine (`build_fastpath.py`)**: Cross-platform support for GCC, Clang, and MSVC (`cl.exe`) across Windows (`.dll`), Linux (`.so`), and macOS (`.dylib` / `.so`) with 30s timeout guards.
  - **Transparent JIT Compilation**: `src/fastpath.py` automatically detects and compiles `fastpath.c` on first import when cloning from Git without a prebuilt library.
  - **Pip Packaging Hooks**: `setup.py` custom `BuildPyWithFastpath` and `DevelopWithFastpath` commands compile native C hot paths during `pip install .` and `pip install -e .`.
  - **Distribution Manifest**: Added `MANIFEST.in` and updated `pyproject.toml` to package all 70 modules and native binary artifacts.
- **Dual Execution Mode Testing**:
  - Full automated suite runs in default mode (With FastPath: 620/620 passed in ~69s).
  - Full automated suite runs in fallback mode via `MDRAP_DISABLE_FASTPATH=1` (Without FastPath: 597 passed, 7 skipped in ~70s).
- **Expanded Quantitative Ecosystem (70 Modules)**:
  - Options pricing engine (`src/options.py`), portfolio risk manager (`src/risk.py`), feature store (`src/features.py`), backtesting engine (`src/backtest.py`), persistent bar database (`src/bardb.py`), news & sentiment pipeline (`src/news.py`), alerting engine (`src/alerts.py`), corporate actions (`src/corporate_actions.py`), FIX engine (`src/fix_engine.py`), and quantitative trading CLI (`src/trading_cli.py`).

### Optimized & Leaned
- **Codebase Consolidation**:
  - Merged `src/chd_cli.py` (132 lines) into `src/chd.py` with backward-compatible shim.
  - Merged `src/sdk_dashboard.py` (184 lines) into `src/terminal_display.py` with backward-compatible shim.
  - Merged `src/sdk/client.py` (142 lines) into `src/client.py` with backward-compatible shim.
  - Reduced ~250 lines of argparse subparser boilerplate in `src/cli.py` via `_sub()`.
  - Cached `git rev-parse` lookups in `pipeline.py` and `pipeline_v2.py`, eliminating 25-50 ms kernel delay per pipeline instance.
  - Deduplicated L2 depth and VWAP event parsing via `_load_or_fetch_depth_events()`.
- **Removed Non-Public Files**:
  - Pruned internal strategy docs (`mdrap_launch_plan.md`, `mdrap_open_core_strategy.md`).
  - Cleaned binary profiling dumps (`benchmarks/*.prof`), test residue (`.coverage`, `test_dest.tmp`), and outdated distribution builds (`dist/`).
  - Added `dist/`, `build/`, and `*.egg-info/` to `.gitignore`.

### Security & Hardening
- Subprocess safety: all compiler and git invocations use argument vectors, `shell=False`, and strict timeout limits.
- Memory and buffer bounds enforcement across 8,192 symbols and 32 sources in `fastpath.c`.
- Verified mitigation of XXE, Billion Laughs, SSRF, and timing side-channel attacks across 18 automated security tests.
- 620 automated tests passing with zero regressions.

---

## [1.2.0] - 2026-09-12
**Institutional Alternative Data & Native C Spatial Fastpath**

Expanded MDRAP from high-frequency tick market data into institutional corporate intelligence, maritime geopolitical tracking, and sub-microsecond compiled spatial algorithms.

### Added
- **SEC EDGAR Alternative Data Engine (`src.research`)**:
  - Real-time Form 8-K material corporate event taxonomy (Item 5.02 executive moves, Item 2.02 earnings, Item 1.01 contracts) classified by urgency.
  - Official Form 4 XML insider transaction parser tracking officer and director purchases, sales, prices, and post-trade share holdings.
  - Audited GAAP facts integration pulling XBRL balance sheet and income metrics directly from SEC servers.
  - Multi-tier in-memory and atomic disk caching (`data/edgar_cache/`) with 300s TTL.
- **Global Maritime Tanker & Cargo Tracking Engine (`src.vessel`)**:
  - Real-time global tracking of commercial crude oil tankers (VLCC/ULCC), LNG carriers, dry bulkers, and container vessels.
  - Commercial fleet owner (Frontline, Euronav, DHT, Maersk, COSCO) and chartering commodity major tagging (Saudi Aramco, Shell, BP, Vitol, Trafigura, Vale).
  - Geopolitical geofencing for 8 primary maritime chokepoints (Strait of Hormuz, Malacca, Suez, Bab-el-Mandeb, Panama, Bosphorus, Cape of Good Hope, Dover Strait).
  - Floating commodity breakdown and seaborne cargo volume attribution.
- **Tier-2 Native C Vectorized Geodesic & Spatial Fastpath (`src.fastpath`)**:
  - Recompiled GCC 14 `-O3` native C shared library (`src/fastpath.dll`).
  - Axis-Aligned Bounding Box (AABB) branchless spatial pre-filter rejecting distant points in ~1 CPU cycle (~0.3 ns).
  - Vectorized Structure-of-Arrays (SoA) batch geofencing evaluating 10,000 vessels across 8 chokepoints (80,000 checks) in **23.08 ms (~288 ns per chokepoint check)**.
  - Zero-error transparent fallback to pure Python if native library is absent.
- **Shorthand CLI Mnemonic Routing**: Smart argument rewriting for `mdrap edgar AAPL`, `mdrap company AAPL`, `mdrap vessel "FRONT ALTAIR"`, `mdrap tankers`.

### Security & Hardening
- **SSRF Guard**: Strict allowlist enforcing HTTPS to `data.sec.gov` and `www.sec.gov` on port 443 with traversal and cloud metadata blocking.
- **XXE & Billion Laughs Mitigation**: 2 MB ceiling and recursive 25-level XML depth limitation.
- **Path Disclosure Redaction**: Regex sanitizer scrubbing Windows and POSIX developer directories from CLI output.
- **Adversarial Coordinates Fuzzing**: Safe handling of NaN, Inf, coordinate overflows, negative speeds, and invalid circular headings.
- **486 Automated Tests**: Test suite expanded from 238 to 486 automated unit, stress, adversarial, and fastpath tests (100% passing).

---

## [1.0.0] - 2026-09-08
**The Initial Stable Release**

MDRAP has officially matured from a testing framework into a fully battle-tested institutional execution platform.

### Added
- **TCP Gateway & SDK (`mdrap.sdk`)**: Ultra-low latency asynchronous TCP stream pushing canonical events to algorithmic clients. 
- **Algorithmic Execution Sandbox (`src.sdk.execution`)**: A paper-trading harness for quants to write algorithms (`Strategy`), calculate live BBO slippage, and track PnL seamlessly.
- **Terminal Visualizer (`mdrap dashboard`)**: High-performance, zero-install TUI using `rich` for tracking VWAP execution curves, Live Book Spreads, and throughput telemetrics.
- **DuckDB Integration (`src.columnar`)**: Real-time columnar analytics integration for sub-10ms VWAP & Volume Profile aggregations over millions of rows, with concurrent Parquet file replication to guarantee zero-locking.

### Optimized
- Completely stripped out blocking synchronous database flushes. The hot path now leverages `SQLite WAL` (Write-Ahead Logging) and `duckdb.appender` C++ vector paths for blistering ingest speeds (>100x improvement).
- TCP Socket Backpressure completely separates network loop threads from blocking Pandas computations via `asyncio.Queue` and background tasks. 

### Security & Hardening
- 238 Automated Tests running through chaos injection, deterministic multi-threading boundaries, and zero-memory leak verifications.

---
_MDRAP: Financial market infrastructure for converting noisy feeds into validated, deterministic streams._
