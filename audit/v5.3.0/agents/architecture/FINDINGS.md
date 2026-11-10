# Architecture & System Design — Detailed Findings

## Executive Summary
Audited pipeline data flow, module dependency graph, tier separation, entry points, configuration management, package structure, version consistency, and companion stubs. The core engine exhibits high low-latency engineering maturity in deterministic WAL ingestion and C fastpath evaluation, but suffers from version desynchronization (4.0.1 vs 5.3.0), configuration schema divergence, companion contract failures, and dead build code.

## Categorized Findings

### CRITICAL
1. **F-ARCH-01: Version Inconsistency (4.0.1 in Code vs 5.3.0 in Docs)**:
   - `src/mdrap/_version.py:8` reports `__version__ = "4.0.1"`.
   - `pyproject.toml` uses `dynamic = ["version"]` which resolves to `4.0.1`.
   - `CHANGELOG.md` documents up to `[5.3.0] - 2026-11-05`.
   - `/health` endpoint exposes `version: 4.0.1`.
   - `test_c04_version_consistency` in `tests/test_docs_verification.py` hardcodes assertion that version starts with `4.0`.
   - `claims_manifest.json` certifies `4.0.1`.
   - **Impact**: Any user installing the package receives 4.0.1, conflicting with all v5 documentation.

2. **F-ARCH-02: Missing Contract Classes in Options Fallback Shim Crashing CLI**:
   - `src/mdrap/trading_cli.py:302` imports `OptionContract, OptionType, ExerciseStyle, price_option, OptionsChain` from `.options`.
   - `src/mdrap/options.py` only defines `OptionsChain`, `BlackScholes`, `BinomialTree`.
   - **Impact**: Invoking `mdrap options price` raises an unhandled `ImportError`.

3. **F-ARCH-03: Fatal AttributeError in `mdrap.backtest` When Running on Stubs**:
   - `src/mdrap/backtest.py:101` invokes `runner.strategy.on_start()`.
   - When `mdrap_strategies` is absent, `StrategyRunner` is an empty pass-through stub class whose `__init__` does nothing, lacking `strategy`.
   - **Impact**: `BacktestEngine.run()` crashes with `AttributeError`.

### HIGH
4. **F-ARCH-04: Dual Configuration Loaders & Schema Incompatibility**:
   - Two competing config files: `config.yaml` (`src/mdrap/config.py`) and `mdrap.toml` (`src/mdrap/config_loader.py`).
   - `QualityEngine` and `FastQualityEngine` load `mdrap.toml`; other components load `config.yaml`.
   - `config_loader.py:56` strictly restricts top-level sections to `ALLOWED_TOP_SECTIONS = {"defaults", "venue", "system", "storage", "network", "security"}`.
   - Any attempt to configure `bbo`, `feeds`, `columnar`, or `watchdog` in `mdrap.toml` causes `ConfigValidationError`.

5. **F-ARCH-05: Missing `rich` Dependency Silently Drops 12 Trading CLI Subcommands**:
   - `src/mdrap/trading_cli.py:15-17` imports directly from `rich` instead of internal `mdrap.term`.
   - `src/mdrap/cli/__init__.py:1937-1942` wraps the import in `except Exception: pass`.
   - In a stdlib-only core deployment, 12 subcommands (`backtest`, `risk`, `bars`, `options`, `news`, `alert`, `watchlist`, `portfolio`, `corpact`, `features`, `schedule`, `markets`) silently disappear from the CLI.

### MEDIUM
6. **F-ARCH-06: Accumulation of Dead and Deprecated Companion Stubs**:
   - Modules extracted to companion packages (`options.py`, `vessel.py`, `tca.py`, `strategy_sdk.py`, `fx.py`, `risk.py`) remain in `src/mdrap/` as pass-only classes.
   - Requires 7 filter warnings in `pyproject.toml` to suppress deprecation warnings during testing.

7. **F-ARCH-07: Dead Code in Setuptools Build Customization**:
   - `src/mdrap_build.py:8-35` defines `_CORE_MODULES` set, but it is never referenced in `CleanBuildPy`. Core wheels package all 107 modules indiscriminately.

### LOW / INFO
8. **F-ARCH-08: Advertised Kernel-Bypass Ingress (AF_XDP/DPDK) Is Passive Blueprint Only**:
   - `src/mdrap/network.py:54-78` defines dataclasses `AFXDPConfig` and `DPDKConfig` with zero packet ingestion logic.
9. **F-ARCH-09: Overwritten Struct Assignment in `src/mdrap/eurex.py`**:
   - Line 27 sets `EUREX_HEADER_STRUCT = struct.Struct("<HIH")` which is immediately overwritten by line 29 with `struct.Struct("<IHI")`.\n