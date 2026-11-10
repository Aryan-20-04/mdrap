"""
Script to materialize all 9 multi-agent audit reports into audit/v5.3.0/agents/
Each agent gets FINDINGS.md, EVIDENCE.md, and RECOMMENDATIONS.md.
"""
from pathlib import Path

BASE = Path(r"c:\Users\KIIT0001\Desktop\Projects\mdrap\audit\v5.3.0\agents")

AGENTS = {
    "architecture": {
        "title": "Agent 1: Architecture & System Design",
        "findings": """# Architecture & System Design — Detailed Findings

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
   - Line 27 sets `EUREX_HEADER_STRUCT = struct.Struct("<HIH")` which is immediately overwritten by line 29 with `struct.Struct("<IHI")`.
""",
        "evidence": """# Architecture & System Design — Exact Evidence

## 1. Version Inconsistency
- File: `src/mdrap/_version.py:8`
  ```python
  __version__ = "4.0.1"
  ```
- File: `pyproject.toml:6, 128`
  ```toml
  dynamic = ["version"]
  [tool.setuptools.dynamic]
  version = {attr = "mdrap._version.__version__"}
  ```
- File: `CHANGELOG.md:6-30`
  ```markdown
  ## [5.3.0] - 2026-11-05
  ...
  ## [5.0.0] - 2026-10-31
  ```
- File: `src/mdrap/api.py:99`
  ```python
  class HealthResponse(BaseModel):
      status: str = "healthy"
      version: str = __version__
  ```
- File: `audit/claims_manifest.json:2`
  ```json
  "mdrap_version": "4.0.1"
  ```

## 2. Options Fallback Shim Crashing CLI
- File: `src/mdrap/trading_cli.py:302`
  ```python
  from .options import OptionContract, OptionType, ExerciseStyle, price_option, OptionsChain
  ```
- File: `src/mdrap/options.py:46-59`
  ```python
  class OptionsChain:
      def __init__(self, *args, **kwargs):
          raise NotImplementedError("OptionsChain requires the 'mdrap_options' companion package...")
  class BlackScholes: pass
  class BinomialTree: pass
  __all__ = ["OptionsChain", "BlackScholes", "BinomialTree"]
  ```

## 3. Backtest Crash on StrategyRunner Stub
- File: `src/mdrap/backtest.py:100-101`
  ```python
  runner = StrategyRunner(strategy)
  runner.strategy.on_start()
  ```
- File: `src/mdrap/strategy_sdk.py:63-65`
  ```python
  class StrategyRunner:
      def __init__(self, *args, **kwargs):
          pass
  ```

## 4. Configuration Schema Restriction
- File: `src/mdrap/config_loader.py:56`
  ```python
  ALLOWED_TOP_SECTIONS = {"defaults", "venue", "system", "storage", "network", "security"}
  ```

## 5. Rich Dependency in CLI
- File: `src/mdrap/trading_cli.py:15-17`
  ```python
  from rich.console import Console
  from rich.panel import Panel
  from rich.table import Table
  ```
- File: `src/mdrap/cli/__init__.py:1937-1942`
  ```python
  try:
      from ..trading_cli import add_trading_parsers
      add_trading_parsers(sub)
  except Exception:
      pass
  ```
""",
        "recommendations": """# Architecture & System Design — Recommendations

## Priority 0 (Release Blockers)
1. **Reconcile Package Version Metadata**:
   - Update `__version__ = "5.3.0"` in `src/mdrap/_version.py:8`.
   - Update version assertions in `tests/test_docs_verification.py`.
   - Update version in `audit/claims_manifest.json` and `README.md` badge.
   - Run `tools/check_docs.py` to confirm alignment.
2. **Fix Missing Contract Classes in `src/mdrap/options.py`**:
   - Define `OptionContract`, `OptionType`, `ExerciseStyle`, `price_option` in `src/mdrap/options.py` raising `CompanionPackageMissing("mdrap_options")`.
3. **Fix Fail-Closed Invariant in `src/mdrap/backtest.py`**:
   - Guard `runner.strategy.on_start()` with checks that `runner.strategy` exists.

## Priority 1 (Architecture & Stability)
4. **Unify Configuration Schema**:
   - Consolidate on `mdrap.toml` as single source of truth.
   - Add `bbo`, `feeds`, `columnar`, `watchdog` to `ALLOWED_TOP_SECTIONS` in `src/mdrap/config_loader.py`.
5. **Decouple `trading_cli.py` from Third-Party `rich`**:
   - Import `Console`, `Table`, `Panel` from internal `mdrap.term`.
   - Log warnings instead of swallowing errors when optional CLI modules fail to import.

## Priority 2 (Code Hygiene)
6. **Clean Up Companion Stubs in Core**:
   - Implement clean `__getattr__` on companion stub modules that consistently raises `CompanionPackageMissing`.
   - Remove dead code `_CORE_MODULES` in `src/mdrap_build.py`.
7. **Fix Shadowed Struct in `src/mdrap/eurex.py`**:
   - Delete dead line 27.
"""
    },
    "market_data": {
        "title": "Agent 2: Market Data & Live Feed Integrity",
        "findings": """# Market Data & Live Feed Integrity — Detailed Findings

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
    - `src/mdrap/bbo.py:187-252` and `src/mdrap/quality.py:240-244` rigorously track venue attribution and enforce `INVALID > SUSPICIOUS > VALID` priority.
""",
        "evidence": """# Market Data & Live Feed Integrity — Exact Evidence

## 1. Databento Stream Fragmentation
- File: `src/mdrap/databento_feed.py:526-533`
  ```python
  while offset < total_len and not self._stop_event.is_set():
      ev, consumed = decode_dbn_record(chunk, offset, self.resolver)
      if consumed <= 0:
          break
      offset += consumed
      self._stats["records_decoded"] += 1
      if ev:
          self._enqueue_event(ev)
  ```

## 2. Databento Plaintext Auth
- File: `src/mdrap/databento_feed.py:568-582`
  ```python
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.connect((host, port))
  _greeting = s.recv(1024).decode("utf-8", errors="replace")
  auth_payload = f"auth={self.api_key}|dataset={self.dataset}|schema={self.schema}\\n"
  s.sendall(auth_payload.encode("utf-8"))
  ```

## 3. Missing `websockets` Dependency
- File: `pyproject.toml:39-44`
  ```toml
  dependencies = []
  ```
- File: `src/mdrap/ws_feed.py:598-602`
  ```python
  if not HAS_WEBSOCKETS:
      logger.error("[ws_feed] 'websockets' library not installed. WebSocket streaming disabled.")
      return
  ```

## 4. Fabricated L2 Depth in `live.py`
- File: `src/mdrap/live.py:855-863`
  ```python
  bids_l2 = [
      [round(bid_p - i * spread_offset, 2), float(100 * (i + 1))]
      for i in range(5)
      if round(bid_p - i * spread_offset, 2) > 0
  ]
  asks_l2 = [
      [round(ask_p + i * spread_offset, 2), float(100 * (i + 1))]
      for i in range(5)
  ]
  ```

## 5. Unbounded REST Polling
- File: `src/mdrap/live.py:1032, 1108`
  ```python
  poll_interval_s: float = 0.05
  ...
  time.sleep(poll_interval_s)
  ```
""",
        "recommendations": """# Market Data & Live Feed Integrity — Recommendations

## Priority 0 (Release Blockers)
1. **Fix Databento Stream Fragmentation (CRIT-01)**:
   - Introduce an internal byte buffer (`self._residual_buffer = bytearray()`) in `DatabentoFeedManager`.
   - Prepend residual bytes to incoming socket chunks before calling `decode_dbn_record()`.
   - Retain unconsumed trailing bytes when `consumed == 0`.
2. **Implement TLS and CRAM Handshake for Databento (CRIT-02)**:
   - Wrap socket with `ssl.create_default_context().wrap_socket()`.
   - Parse greeting challenge and compute CRAM HMAC response.
3. **Declare `websockets` in `pyproject.toml` (HIGH-01)**:
   - Add `"websockets>=12.0,<17.0"` to `pyproject.toml` optional dependencies and dev/test requirements.

## Priority 1 (High Priority Fixes)
4. **Disallow Silent Mock Fallback in `polygon_feed.py` (MED-02)**:
   - Raise `ImportError` if `mock_mode=False` and `websockets` is missing.
5. **Add HTTP 429 Rate-Limiting & Backoff to `live.py` (HIGH-03)**:
   - Implement per-venue token bucket rate limits and inspect `Retry-After` headers.
6. **Demarcate or Remove Fabricated L2 Depth (HIGH-02)**:
   - Set `bids: []` and `asks: []` unless real top-of-book quotes are present.

## Priority 2 (Hygiene & Robustness)
7. **Fix Kraken Ticker Slicing (LOW-01)**:
   - Use `resolve_venue_symbols(symbol)["kraken"]` rather than naive string slicing.
8. **Add Optional Live Network Integration Tests (MED-03)**:
   - Add `@pytest.mark.live_network` tests for public endpoints.
"""
    },
    "performance": {
        "title": "Agent 3: Performance & Latency Engineering",
        "findings": """# Performance & Latency Engineering — Detailed Findings

## Executive Summary
Audited benchmark infrastructure, hot path micro-optimizations, throughput claims vs raw committed data, memory bounds, SQLite batch operations, and pure Python fallback parity. The compiled C kernel (`fastpath.c`) demonstrates exemplary AVX2/SSE4.2 pre-fetching and fixed-point arithmetic. However, a CRITICAL semantic divergence exists between Python and C on `HALT` and `STATUS` events, and benchmark documentation inflates throughput by 22.7% over raw committed results.

## Categorized Findings

### CRITICAL
1. **F-PERF-01: Semantic Divergence on `HALT` and `STATUS` Events (C Rejecting Valid Events)**:
   - `src/mdrap/quality.py:360-366, 390-392`: Pure Python marks `HALT` and `STATUS` events as `QualityStatus.VALID`.
   - `src/mdrap/fastpath.py:1232-1240`: Maps `HALT`/`STATUS` to `c_ev_type = -1`.
   - `src/mdrap/fastpath.c:677-680`: Unconditionally marks `ev->event_type != 0 && ev->event_type != 1` as `STATUS_INVALID` with `REASON_SCHEMA_VIOLATION`.
   - **Impact**: Pure Python marks trading halts as `VALID`; Native C fastpath marks them as `INVALID`. Violates GEMINI.md Rule 4 (Pure Python Fallback Parity).

### HIGH
2. **F-PERF-02: Published Benchmarks Inflate Committed Raw Data by 22.7%**:
   - `README.md` and `docs/benchmarks.md` publish:
     - Native C Fastpath: **510,820 EPS** (p50: 1.60 µs)
     - Pure Python Fallback: **170,080 EPS** (p50: 8.30 µs)
     - Speedup: **3.00x**
   - Actual committed data in `audit/v5.3/raw/benchmarks_raw.json`:
     - Native C Fastpath: **416,331.2 EPS** (p50: 1.70 µs)
     - Pure Python Fallback: **149,885.7 EPS** (p50: 4.40 µs)
     - True speedup: **2.78x**
   - Published numbers are inflated by **+22.7%** (C) and **+13.5%** (Python).

3. **F-PERF-03: Invalid Reproduction Command in README**:
   - `README.md:156` cites `python benchmarks/bench_scale.py` to reproduce 43.67M EPS Native C kernel.
   - `bench_scale.py` runs Python `Engine.submit()` at 20k–50k EPS; does not test isolated C kernel.

4. **F-PERF-04: Redundant `json.dumps()` on Every Single Ingestion Tick**:
   - `src/mdrap/pipeline.py:787-791`: `isinstance(event.raw_id, int)` evaluates to `False` on 100% of ticks because `raw_id` is a string (`raw-RUNID-N`).
   - Executes `json.dumps([event.raw_id])` for every processed event on the hot path.

5. **F-PERF-05: Missing Backpressure Stall Telemetry in Pipeline**:
   - `src/mdrap/pipeline.py:1163-1175`: Catches `queue.Full` on saturated write queue, but never invokes `self.metrics.record_streaming(..., backpressure_stall=True)`. Telemetry reports 0 stalls while dropping batches.

### MEDIUM
6. **F-PERF-06: Unbounded Dictionary Growth in `FastQualityEngine`**:
   - `src/mdrap/fastpath.py:1101-1104`: `self._inst_map` stores instrument strings indefinitely without eviction, unlike `QualityEngine` which bounds tracked instruments at 100,000.

7. **F-PERF-07: Synthetic Loop Reported as Ingress Benchmark**:
   - `benchmarks/run_v53_unified_benchmark.py:72-77`: Boundary A measures Python list comprehension traversal of pre-allocated list, claiming 16.1M EPS "ingress wire parsing".

### LOW / INFO
8. **F-PERF-08: Disk Durability and Batch Operations Verified**:
   - `src/mdrap/storage.py` is 100% compliant with `executemany`, WAL mode, 256MB mmap, and atomic batch transactions.
""",
        "evidence": """# Performance & Latency Engineering — Exact Evidence

## 1. Parity Divergence on HALT / STATUS
- File: `src/mdrap/quality.py:360-366`
  ```python
  is_status = event.event_type in (EventType.HALT, EventType.STATUS, "HALT", "STATUS")
  if not is_trade and not is_quote and not is_status:
      self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
      return event
  ```
- File: `src/mdrap/fastpath.py:1232-1240`
  ```python
  c_ev_type = (
      1 if (event.event_type == EventType.QUOTE or event.event_type == "QUOTE")
      else (0 if (event.event_type == EventType.TRADE or event.event_type == "TRADE") else -1)
  )
  ```
- File: `src/mdrap/fastpath.c:677-680`
  ```c
  if (ev->event_type != 0 && ev->event_type != 1) {
      mark(res, STATUS_INVALID, REASON_SCHEMA_VIOLATION);
      return;
  }
  ```

## 2. Benchmark Raw Discrepancy
- File: `audit/v5.3/raw/benchmarks_raw.json:4-24`
  ```json
  "boundary_c_quality_c_fastpath_eps": 416331.2,
  "boundary_c_quality_python_fallback_eps": 149885.7,
  "quality_c_fastpath_speedup": 2.7776579607861304
  ```
- File: `README.md:157-158`
  ```markdown
  | **Native C Fastpath FFI** | ... | **510,820** |
  | **Pure Python Fallback Parity** | ... | **170,080** |
  ```
""",
        "recommendations": """# Performance & Latency Engineering — Recommendations

## Priority 0 (Release Blockers)
1. **Fix Fallback Parity for `HALT` and `STATUS` Events (F-PERF-01)**:
   - In `src/mdrap/fastpath.c:677`, accept `ev->event_type == 2` (HALT) and `ev->event_type == 3` (STATUS).
   - In `src/mdrap/fastpath.py:1232`, map `EventType.HALT` to 2 and `EventType.STATUS` to 3.
   - Add test vectors in `tests/test_golden_parity.py` asserting identical `VALID` verdicts for HALT/STATUS across Python and C.
2. **Align Benchmark Documentation with Committed Raw Data (F-PERF-02, F-PERF-03)**:
   - Update `README.md` and `docs/benchmarks.md` to reflect 416.3k EPS Native C, 149.9k EPS Pure Python, 2.78x speedup.
   - Fix reproduction command for the isolated C kernel in `README.md`.

## Priority 1 (Hot Path Optimizations)
3. **Eliminate Hot Path `json.dumps()` in Pipeline (F-PERF-04)**:
   - Pre-format string raw IDs via `f'["{event.raw_id}"]'` or store raw IDs as monotonic integers.
4. **Record Backpressure Telemetry (F-PERF-05)**:
   - Call `self.metrics.record_streaming(..., backpressure_stall=True)` upon queue full timeout.

## Priority 2 (Memory Bounds)
5. **Bound `_inst_map` in `FastQualityEngine` (F-PERF-06)**:
   - Apply LRU capacity limit matching `QualityEngine.max_tracked_instruments` (100,000).
"""
    },
    "native_concurrency": {
        "title": "Agent 4: Native C, SIMD & Concurrency",
        "findings": """# Native C, SIMD & Concurrency — Detailed Findings

## Executive Summary
Audited C source files (`fastpath.c`, `mdrap_core.c`, `_fastpath_c.c`), build system (`build_fastpath.py`), shared memory IPC (`shm.py`), and pipeline concurrency. The native acceleration layer features high-performance cache-line aligned seqlocks and SPSC circular queues. However, 3 CRITICAL defects were identified: multithreaded race conditions in pipeline batching, and MSVC build breakages in `_fastpath_c.c` and `mdrap_core.c`.

## Categorized Findings

### CRITICAL
1. **F-NAT-01: Multithreaded Race Condition Between Background Writer and Tick Ingestion**:
   - `src/mdrap/pipeline.py:388-396` and `1147-1158`: Background writer thread `_writer_loop` calls `flush()` which checks and reassigns `self._canonical_batch, self._quarantine_batch, self._lineage_batch = [], [], []`.
   - Main thread simultaneously calls `_enqueue_canonical()` appending to `self._canonical_batch` without any lock.
   - **Impact**: Lost events, torn batch state, list resizing race conditions under real load.

2. **F-NAT-02: MSVC Build Breakage in C-API Extension (`_fastpath_c.c`)**:
   - `src/_fastpath_c.c:515-574`: Directly invokes GCC/Clang built-ins `__atomic_load_n` and `__atomic_store_n`.
   - Missing MSVC atomic shims.
   - **Impact**: Fails compilation on Windows with MSVC (`error C3861: '__atomic_load_n': identifier not found`).

3. **F-NAT-03: MSVC Build Breakage in Standalone Daemon (`mdrap_core.c`)**:
   - `src/mdrap/mdrap_core.c:102-107, 122-124`: Includes `<x86intrin.h>` and `<cpuid.h>` which do not exist in MSVC runtime.
   - **Impact**: Cannot compile `mdrap-core.exe` on Windows using MSVC.

### HIGH
4. **F-NAT-04: Unbounded IPC Latency — Head Sequence Published Only Every 32 Ticks**:
   - `src/mdrap/mdrap_core.c:487-493`: Only updates SHM head sequence when `(seq & 0x1F) == 0`.
   - **Impact**: Low-frequency tickers or quiet feeds hold back up to 31 ticks of events from downstream readers.

5. **F-NAT-05: Data Race & Torn Reads in Native Replay Buffer**:
   - `src/mdrap/fastpath.c:1019-1080`: `fastpath_engine_replay_record` and `fastpath_engine_replay_slice` omit locks and seqlocks.
   - **Impact**: Concurrent reads observe torn struct fields and data races.

6. **F-NAT-06: Fatal CPython SystemError — Returning PyObject With Pending Exception**:
   - `src/_fastpath_c.c:232-255, 348-373`: Fails to check `PyErr_Occurred()` after argument conversions and returns objects with exceptions set.

7. **F-NAT-07: Concurrent Scratch Buffer Corruption in `evaluate_batch()`**:
   - `src/mdrap/fastpath.py:1351-1358`: `_batch_c_events` and `_batch_c_results` are shared instance variables. When `thread_safe=False` (default), concurrent callers corrupt each other's memory.

8. **F-NAT-08: Catastrophic Floating-Point Cancellation in MSVC Variance Fallback**:
   - `src/mdrap/fastpath.c:871-880`: On platforms lacking `__int128`, falls back to naive $\\frac{\\sum X^2}{N} - (\\bar{X})^2$ in 64-bit float, losing all precision on large prices.

### MEDIUM
9. **F-NAT-09: Misleading Claim of SIMD SoA Evaluation**:
   - `src/mdrap/fastpath.c:883-968`: Advertised as SIMD batch evaluation, but executes an unvectorized scalar loop.
10. **F-NAT-10: Illegal Instruction (SIGILL) Risk — Missing Runtime CPUID Check**:
    - `src/mdrap/mdrap_core.c:458`: Executes AVX2 instructions based solely on `#ifdef __AVX2__` without runtime CPUID validation.
""",
        "evidence": """# Native C, SIMD & Concurrency — Exact Evidence

## 1. Pipeline Race Condition
- File: `src/mdrap/pipeline.py:388-396` (Writer Thread)
  ```python
  if (self._canonical_batch or self._quarantine_batch or self._lineage_batch) and ...:
      self.flush(wait=False)
  ```
- File: `src/mdrap/pipeline.py:432-440` (Ingest Thread)
  ```python
  def _enqueue_canonical(self, event: CanonicalEvent) -> None:
      self._canonical_batch.append(event)
  ```
- File: `src/mdrap/pipeline.py:1152-1158` (Flush)
  ```python
  canon, quar, lin = self._canonical_batch, self._quarantine_batch, self._lineage_batch
  self._canonical_batch, self._quarantine_batch, self._lineage_batch = [], [], []
  ```

## 2. Missing MSVC Atomics in `_fastpath_c.c`
- File: `src/_fastpath_c.c:515, 523`
  ```c
  tail = __atomic_load_n(&r->tail, __ATOMIC_ACQUIRE);
  __atomic_store_n(&r->head, head + 1, __ATOMIC_RELEASE);
  ```

## 3. Invalid MSVC Headers in `mdrap_core.c`
- File: `src/mdrap/mdrap_core.c:103-105`
  ```c
  #include <x86intrin.h>
  #include <immintrin.h>
  #include <cpuid.h>
  ```
""",
        "recommendations": """# Native C, SIMD & Concurrency — Recommendations

## Priority 0 (Release Blockers)
1. **Protect Pipeline Batches with Lock (F-NAT-01)**:
   - Introduce `self._batch_lock = threading.Lock()` in `Pipeline` protecting `_canonical_batch`, `_quarantine_batch`, `_lineage_batch`.
2. **Add MSVC Atomics to `_fastpath_c.c` (F-NAT-02)**:
   - Add `#if defined(_MSC_VER)` shims using `_InterlockedExchange64` and `_InterlockedCompareExchange64`.
3. **Fix Windows Headers in `mdrap_core.c` (F-NAT-03)**:
   - Guard `<x86intrin.h>` behind `#if !defined(_MSC_VER)` and use `<intrin.h>` with `__cpuid` on MSVC.

## Priority 1 (Concurrency & IPC)
4. **Publish Head Sequence Per-Tick (F-NAT-04)**:
   - In `mdrap_core.c`, publish head sequence on every tick or after microsecond timeout.
5. **Thread-Safe Replay Ring (F-NAT-05)**:
   - Protect `replay_ring` writes and reads with `MD_LOCK(&eng->lock)` or seqlock.
6. **Check `PyErr_Occurred()` in C Extension (F-NAT-06)**:
   - Ensure clean error handling before returning PyObjects.
7. **Thread-Local Scratch Arrays in `FastQualityEngine` (F-NAT-07)**:
   - Use `threading.local()` for ctypes scratch buffers or lock `evaluate_batch()`.
"""
    },
    "persistence": {
        "title": "Agent 5: Persistence, WAL & Data Integrity",
        "findings": """# Persistence, WAL & Data Integrity — Detailed Findings

## Executive Summary
Audited `storage.py`, `storage_schema.py`, `ingestlog.py`, `journal.py`, `journal_replicator.py`, `projection.py`, `recovery.py`, and quarantine subsystems. WAL framing, length collars, CRC32 v2 hashing, group commit fsync policies, and Merkle quarantine logging are robust. However, 2 CRITICAL defects were discovered: JournalReplicationServer silently drops frames while returning fake ACKs, and Engine recovery crashes on pruned WAL segments due to hardcoded `iter_from(0)`.

## Categorized Findings

### CRITICAL
1. **F-PER-01: Journal Replication Server Silently Drops Frames and Sends Fraudulent ACKs**:
   - `src/mdrap/journal_replicator.py:224-230`: In `_handle_client`:
     ```python
     if msg_type == MSG_FRAME_DATA:
         if self.target_log and hasattr(self.target_log, "append_raw"):
             self.target_log.append_raw(payload)
         ack_frame = pack_replication_frame(MSG_ACK, seq, epoch)
         sock.sendall(ack_frame)
     ```
   - `target_log` is an `IngestLog` instance. `IngestLog` has **no `append_raw` method**.
   - `hasattr()` evaluates to `False`. The server drops the payload without writing it, but sends `MSG_ACK` to the client.
   - **Impact**: Standby node has zero data on failover, causing total data loss.

2. **F-PER-02: Hardcoded `iter_from(0)` in Engine Projection Recovery Crashes on Pruned WAL**:
   - `src/mdrap/engine.py:843, 942, 992, 1017`: Four recovery methods call `self.log.iter_from(0)`.
   - `src/mdrap/ingestlog.py:1488-1493`: Raises `RecoveryError` if `start_offset < segments[0][0]`.
   - When segment retention prunes old segments, `segments[0][0] > 0`.
   - **Impact**: All projection recovery, subscribe, and replay methods crash permanently once pruning has run.

### HIGH
3. **F-PER-03: `AsyncProjectionWorker` Drops Events on Saturated Queue Causing Permanent Assertion Lockout**:
   - `src/mdrap/projection.py:1129-1136`: On `queue.Full`, drops analytical projection events.
   - `src/mdrap/projection.py:581-586`: `SQLiteProjection.apply()` enforces contiguous batch offsets (`batch_start > expected_next` raises `ValueError`).
   - Discards batch after 3 retries; subsequent batches permanently fail because gap is never repaired.

4. **F-PER-04: Replication Daemon Disconnects Every 2 Seconds During Quiet Periods Due to Missing Heartbeats**:
   - `src/mdrap/journal_replicator.py:170, 232`: Server sets 2.0-second socket timeout.
   - Neither client nor server sends keepalive `MSG_HEARTBEAT` frames.
   - **Impact**: Replication link constantly drops and reconnects during quiet trading periods.

### MEDIUM
5. **F-PER-05: Modern `Engine` Architecture Omits Population of `lineage` Table**:
   - In modern `Engine` deployments, `lineage` table remains empty because `EngineDecision` only produces canonical events and quarantine rows.
   - `store.event_lineage()` always returns `None`.

6. **F-PER-06: Individual Batch Write Methods in `Store` Omit Transaction Commits**:
   - `src/mdrap/storage.py:544-614`: `write_canonical_batch`, `write_quarantine_batch`, etc. execute `executemany` without `commit()`.
""",
        "evidence": """# Persistence, WAL & Data Integrity — Exact Evidence

## 1. Replication Silent Drop
- File: `src/mdrap/journal_replicator.py:224-230`
  ```python
  if msg_type == MSG_FRAME_DATA:
      if self.target_log and hasattr(self.target_log, "append_raw"):
          self.target_log.append_raw(payload)

      # Return ACK
      ack_frame = pack_replication_frame(MSG_ACK, seq, epoch)
      sock.sendall(ack_frame)
  ```
- File: `src/mdrap/ingestlog.py`
  (Method `append_raw` is completely absent from `IngestLog` class).

## 2. Hardcoded `iter_from(0)` in Engine
- File: `src/mdrap/engine.py:942`
  ```python
  for off, raw in self.log.iter_from(0):
      if off < start_off:
  ```
- File: `src/mdrap/ingestlog.py:1488-1493`
  ```python
  if self.next_offset > 0 and segments and start_offset < segments[0][0]:
      raise RecoveryError(f"WAL history incomplete: requested start_offset {start_offset}...")
  ```

## 3. Projection Gap Lockout
- File: `src/mdrap/projection.py:581-586`
  ```python
  if batch_start > expected_next:
      raise ValueError(f"Projection gap detected for '{self.name}': expected start offset <= {expected_next}, got batch_start {batch_start}")
  ```
""",
        "recommendations": """# Persistence, WAL & Data Integrity — Recommendations

## Priority 0 (Release Blockers)
1. **Fix Replication Server IngestLog Integration (F-PER-01)**:
   - Implement `append_raw(self, frame_bytes: bytes) -> int` on `IngestLog` (`src/mdrap/ingestlog.py`).
   - In `JournalReplicationServer`, verify persistence before sending `MSG_ACK`.
2. **Fix Pruned WAL Replay in `Engine` (F-PER-02)**:
   - In `Engine.recover_projections`, call `self.log.iter_from(start_off)`.
   - In `Engine._replay_projection_batch`, load latest snapshot at or before `from_offset` and replay from snapshot offset.

## Priority 1 (High Priority Fixes)
3. **Add Heartbeat Keepalives to Replication Daemon (F-PER-04)**:
   - Send `MSG_HEARTBEAT` every 500 ms to prevent socket timeout disconnects.
4. **Heal Saturated Queue Gaps in `AsyncProjectionWorker` (F-PER-03)**:
   - Backfill missing offsets from WAL rather than attempting to apply non-contiguous batches.

## Priority 2 (Lineage & Schema)
5. **Wire Lineage Emission in `Engine` (F-PER-05)**:
   - Persist lineage records during `Engine` projection updates.
"""
    },
    "security": {
        "title": "Agent 6: Security & Threat Modeling",
        "findings": """# Security & Threat Modeling — Detailed Findings

## Executive Summary
Audited API security, secrets management, cryptographic algorithms, injection risks, SHM permissions, and CI/CD security. MDRAP demonstrates strong foundational cryptography (HMAC-SHA256, salted token hashing, parameterized SQL queries). However, 3 HIGH severity vulnerabilities were identified: `/metrics` auth bypass via IP spoofing, browser WebSocket incompatibility with incomplete revocation checks, and broken RFC 6962 domain separation in `packages/mdrap-audit`.

## Categorized Findings

### HIGH
1. **F-SEC-01: `/metrics` Authentication Bypass via `X-Forwarded-For` Header Spoofing**:
   - `src/mdrap/api.py:1670-1678` exempts loopback clients (`127.0.0.1`, `::1`) from metrics authentication.
   - `src/mdrap/api.py:72-90` resolves client IP by taking the **leftmost** entry of `X-Forwarded-For`.
   - **Impact**: Any external attacker sending `X-Forwarded-For: 127.0.0.1` through a trusted reverse proxy bypasses Prometheus authentication.

2. **F-SEC-02: Browser WebSocket Incompatibility & Incomplete Revocation Enforcement**:
   - `src/mdrap/api.py:1483-1496`: WebSocket `/v1/events/stream` requires auth headers (`Authorization`, `X-API-Key`). Standard browser JavaScript WebSocket API cannot pass custom HTTP headers.
   - `src/mdrap/api.py:368-378`: `broadcast_event` checks `if tok and not ent: dead_sockets.append(ws)`. If `tok` is None or empty, the check is skipped and data is delivered.

3. **F-SEC-03: Broken RFC 6962 Domain Separation in `packages/mdrap-audit`**:
   - `packages/mdrap-audit/mdrap_audit/__init__.py:81-94`: `build()` computes internal parent hashes without prepending the required `0x01` byte prefix.
   - Incompatible with core engine `src/mdrap/security.py:209-213` which correctly uses `0x01` prefix.

### MEDIUM
4. **F-SEC-04: Unauthenticated `/v1/readiness` & `/v1/health` Contend Storage RLock**:
   - `src/mdrap/api.py:714-715`: `/v1/readiness` acquires `st.store._lock` (the process-wide SQLite write lock). Sustained unauthenticated probes induce pipeline latency spikes.

5. **F-SEC-05: POSIX SHM Permissions Race Condition & Platform Incompatibility**:
   - `src/mdrap/shm.py:236-242`: `os.chmod("/dev/shm/...")` runs *after* file creation, leaving a brief TOCTOU window where unprivileged local users can read memory. Fails on macOS and Windows.

6. **F-SEC-06: Plaintext API Key Retained in Process Heap for Newly Registered Keys**:
   - `src/mdrap/security.py:1057-1072`: `register_api_key` stores `ent.token` (plaintext) in `self._api_keys[tok_hash]`, retaining it in heap memory.

7. **F-SEC-07: Outdated Supported Versions in `SECURITY.md`**:
   - `SECURITY.md:5-9` lists only versions 3.0.x and 3.1.x as supported.

### LOW / INFO
8. **F-SEC-08: Wildcard CORS Default in `.env.example`**:
   - `.env.example:24` sets `MDRAP_CORS_ORIGINS=*`.
9. **F-SEC-09: Verified Security Strengths**:
   - 100% parameterized SQL queries (zero SQL injection).
   - `subprocess.run` uses list arguments with `shell=False` (zero command injection).
   - Constant-time `hmac.compare_digest` used across all token comparisons.
""",
        "evidence": """# Security & Threat Modeling — Exact Evidence

## 1. Metrics Spoofing Bypass
- File: `src/mdrap/api.py:84`
  ```python
  candidate = forwarded.split(",", 1)[0].strip()  # Leftmost entry!
  ```
- File: `src/mdrap/api.py:1673-1678`
  ```python
  is_loopback = client_host in loopback_hosts
  if metrics_auth_required and not is_loopback:
      # Auth required only if NOT loopback
  ```

## 2. Incomplete WebSocket Revocation
- File: `src/mdrap/api.py:376`
  ```python
  if tok and not ent:
      dead_sockets.append(ws)
      continue
  ```

## 3. Broken RFC 6962 Domain Separation
- File: `packages/mdrap-audit/mdrap_audit/__init__.py:88-91`
  ```python
  combined = (left + right).encode("utf-8")  # Missing 0x01 prefix!
  parent = sha256_hex(combined)
  ```
- File: `src/mdrap/security.py:211-213` (Correct Core Implementation)
  ```python
  return hashlib.sha256(b"\\x01" + bytes.fromhex(left_hex) + bytes.fromhex(right_hex)).hexdigest()
  ```
""",
        "recommendations": """# Security & Threat Modeling — Recommendations

## Priority 0 (Release Blockers)
1. **Fix `/metrics` IP Spoofing (F-SEC-01)**:
   - Remove loopback authentication exemptions. Require explicit monitoring API key or inspect rightmost untrusted IP.
2. **Correct RFC 6962 Domain Separation in `mdrap-audit` (F-SEC-03)**:
   - Call `self.hash_internal_node(left, right)` in `build()` and prepend `b"\\x01"` in `InclusionProof.verify()`.
3. **Fail-Closed WebSocket Revocation Check (F-SEC-02)**:
   - Replace `if tok and not ent:` with `if not ent:` in `broadcast_event()`.

## Priority 1 (High Priority Fixes)
4. **Add Browser WebSocket Subprotocol Authentication (F-SEC-02)**:
   - Support `Sec-WebSocket-Protocol: mdrap-token.<token>` or ticket exchange endpoint.
5. **Mitigate Readiness Storage Lock Contention (F-SEC-04)**:
   - Probe database via `read_conn` or cached heartbeat rather than acquiring write lock.
6. **Harden SHM Permissions Creation (F-SEC-05)**:
   - Wrap creation with `umask(0o077)` to guarantee atomic 0600 mode.
"""
    },
    "testing_release": {
        "title": "Agent 7: Testing, Build & Release Engineering",
        "findings": """# Testing, Build & Release Engineering — Detailed Findings

## Executive Summary
Audited test suite architecture (304 files, ~1,735 tests), golden oracle parity tests, CI/CD workflows, build tools, and quality gates. Golden parity and binary serialization tests are rigorous. However, CRITICAL defects were discovered: assertion swallowing in hot-path tests, a rubber-stamp claims gate in `gen_claims.py`, non-portable `-march=native` compiler flags in release wheels, and version drift (4.0.1 vs 5.3.0).

## Categorized Findings

### CRITICAL
1. **F-TST-01: Critical Assertion Swallowing in Hot-Path Parity Test**:
   - `tests/test_fastpath.py:81-86`: In the per-tick Python vs C parity loop:
     ```python
     try:
         assert res_py.quality_status == res_c.quality_status
     except Exception:
         pass
     ```
   - Catches `Exception`, which catches `AssertionError` and silently suppresses parity failures!

2. **F-TST-02: Rubber-Stamp Documentation Claims Gate in `scripts/gen_claims.py`**:
   - `scripts/gen_claims.py:155-157`: When run with `--check`, extracts `current_claim` from markdown but **never asserts equality** against `claim_str`. Unconditionally prints `[PASSED]`.

3. **F-TST-03: Non-Portable Native C Compiler Flags in Wheels**:
   - `src/mdrap/build_fastpath.py:152, 301`: Compiles with `-march=native`.
   - Binary wheels built on GitHub Actions runners bake runner-CPU-specific instructions, triggering `SIGILL` on older client CPUs.

4. **F-TST-04: Version Desynchronization Across Release Manifests**:
   - `src/mdrap/_version.py` is `4.0.1`; `claims_manifest.json` is `4.0.1`; documentation declares `v5.3.0`.

### HIGH
5. **F-TST-05: Missing SDist in PyPI Release Workflow**:
   - `.github/workflows/wheels.yml:55-69`: Builds and publishes binary wheels, but omits source distribution (`.tar.gz`).

6. **F-TST-06: Shared Memory Test Resource Leaks**:
   - `tests/test_shm.py:21-155`: Allocates hardcoded segment names without `try...finally` cleanup. Unhandled assertion leaves segments in `/dev/shm`.

7. **F-TST-07: Incomplete Pure Python Fallback CI Verification**:
   - `.github/workflows/ci.yml:49-53`: Only runs a single test file under `MDRAP_DISABLE_FASTPATH="1"`. 303 test files are never evaluated in pure Python mode in CI.

8. **F-TST-08: Missing Dedicated Unit Tests for Core Production Modules**:
   - Over 20 key modules lack dedicated test files: `network.py`, `supervisor.py`, `profile.py`, `gap.py`, `adapters/`.

### MEDIUM
9. **F-TST-09: Vacuous Assertion in Daemon BBO Query Test**:
   - `tests/test_service.py:68-74`: `if bbo is not None: assert ...`. If daemon is slow to start, `bbo` is `None` and zero assertions run.
10. **F-TST-10: Quality Protocol Gate 4 Excludes Integration Tests**:
    - `scripts/check.py:148-168`: `-k "not integration"` filters out all integration tests, but Gate 5 only runs 3 specific files.
""",
        "evidence": """# Testing, Build & Release Engineering — Exact Evidence

## 1. Assertion Swallowed in `test_fastpath.py`
- File: `tests/test_fastpath.py:81-86`
  ```python
  try:
      assert res_py.quality_status == res_c.quality_status
  except Exception:
      pass
  ```

## 2. Rubber Stamp Gate in `gen_claims.py`
- File: `scripts/gen_claims.py:155-157`
  ```python
  current_claim = match.group(2).strip()
  if args.check:
      print(f"[PASSED] Verified claims manifest for {claim_str}.")
  ```

## 3. `-march=native` in Wheel Flags
- File: `src/mdrap/build_fastpath.py:152`
  ```python
  cmd = [compiler, "-O3", "-march=native", *_get_arch_flags(), ...]
  ```
""",
        "recommendations": """# Testing, Build & Release Engineering — Recommendations

## Priority 0 (Release Blockers)
1. **Remove Bare Except in `test_fastpath.py` (F-TST-01)**:
   - Ensure per-tick status equality assertions fail loudly.
2. **Implement Genuine Assertion in `gen_claims.py` (F-TST-02)**:
   - Assert `claim_str in current_claim`, exiting with code 1 upon mismatch.
3. **Remove `-march=native` from Wheel Build Flags (F-TST-03)**:
   - Use `-O3` with a portable baseline target (`-march=x86-64-v3` or baseline `x86-64`).
4. **Synchronize Package Version to 5.3.0 (F-TST-04)**.

## Priority 1 (CI/CD & Isolation)
5. **Publish SDist in `wheels.yml` (F-TST-05)**:
   - Add `python -m build --sdist` job.
6. **Run Full Test Suite Under Pure Python Fallback in CI (F-TST-07)**.
7. **Harden Shared Memory Test Lifecycle (F-TST-06)**:
   - Wrap SHM allocations in fixtures with deterministic unlink.
"""
    },
    "quantitative_risk": {
        "title": "Agent 8: Quantitative, Trading Risk & Strategy",
        "findings": """# Quantitative, Trading Risk & Strategy — Detailed Findings

## Executive Summary
Audited market microstructure engines, algorithmic strategy SDK, risk management, real-time analytics, and wire protocol decoders. L2 consolidated depth (`depth.py`), L3 MBO (`mbo.py`), ITCH 5.0 (`itch.py`), and SBE wire decoders (`sbe.py`, `cme.py`, `eurex.py`) are fully implemented and mathematically rigorous. However, a CRITICAL broken import in `packages/mdrap-strategies` prevents that package from loading, and high-severity defects exist in backtesting, core pre-trade risk, and FX conversion.

## Categorized Findings

### CRITICAL
1. **F-QNT-01: Broken Import in `packages/mdrap-strategies` Prevents Module Loading**:
   - `packages/mdrap-strategies/mdrap_strategies/__init__.py:1596-1601`:
     ```python
     from .backends import (ExecutionBackend, HistoricalSimulationBackend, LivePaperTradingBackend, ExchangeTestnetBackend)
     ```
   - The file `backends.py` is **missing from the filesystem**.
   - **Impact**: `import mdrap_strategies` crashes with `ModuleNotFoundError`. `src/mdrap/strategy_sdk.py` falls back to stub classes. `tests/test_backtest.py` is completely skipped.

### HIGH
2. **F-QNT-02: Walk-Forward Backtesting Incomplete / Discards 60% of Data Without Model Fitting**:
   - `src/mdrap/backtest.py:370-377`: Partitions window into `train_events` and `test_events`.
   - `train_events` is completely discarded; no parameter optimization or fitting occurs.
   - **Impact**: Provides an illusion of walk-forward optimization while merely running a sliding test window on 40% of the data.

3. **F-QNT-03: Missing Pre-Trade Risk Enforcement & Hollow `PositionLimits` in Core**:
   - `src/mdrap/risk.py:378-385`: `PositionLimits` is an orphaned dataclass with zero integration into order generation or validation. Core has no pre-trade risk gates.

4. **F-QNT-04: Silent 1.0 Parity Fallback on Unknown Currencies in FX Engine**:
   - `src/mdrap/fx.py:86-89`: If a currency pair is unmapped, silently returns `1.0` parity.
   - **Impact**: Unmapped currencies (e.g. JPY, KRW) are valued 1:1 with USD, distorting portfolio valuations.

### MEDIUM
5. **F-QNT-05: `VolatilityTracker` Window Parameter Ignored (Expanding Lifetime Variance)**:
   - `src/mdrap/analytics.py:178-212`: Stores `self.window`, but computes lifetime expanding Welford variance across the entire process lifetime instead of a rolling window.

6. **F-QNT-06: FIX Engine Lacks Socket Layer, Session State Machine, and Gap Recovery**:
   - `src/mdrap/fix_engine.py:160-259`: Overwrites sequence numbers without gap checks; lacks 35=2 Resend Request handling.

7. **F-QNT-07: Indeterminate Event Tie-Breaking in Backtester**:
   - `src/mdrap/backtest.py:82`: Sorts events purely by `exchange_timestamp` without secondary tie-breaker on sequence number.
""",
        "evidence": """# Quantitative, Trading Risk & Strategy — Exact Evidence

## 1. Broken Import in `mdrap-strategies`
- File: `packages/mdrap-strategies/mdrap_strategies/__init__.py:1596-1601`
  ```python
  from .backends import (
      ExecutionBackend,
      HistoricalSimulationBackend,
      LivePaperTradingBackend,
      ExchangeTestnetBackend,
  )
  ```
  (File `packages/mdrap-strategies/mdrap_strategies/backends.py` does not exist).

## 2. Walk-Forward Dummy Implementation
- File: `src/mdrap/backtest.py:370-377`
  ```python
  train_size = int(len(window_events) * train_pct)
  test_events = window_events[train_size:]
  if not test_events:
      continue
  strategy = strategy_factory()
  res = self.run(strategy, test_events)
  results.append(res)
  ```

## 3. Silent 1.0 Parity Fallback
- File: `src/mdrap/fx.py:86-89`
  ```python
  if usd_per_from is None or usd_per_to is None:
      # Unknown currency, assume 1.0 parity
      return 1.0
  ```
""",
        "recommendations": """# Quantitative, Trading Risk & Strategy — Recommendations

## Priority 0 (Release Blockers)
1. **Restore or Implement `backends.py` in `mdrap-strategies` (F-QNT-01)**:
   - Implement the missing backend classes (`ExecutionBackend`, etc.) or clean up imports so `mdrap_strategies` imports cleanly.
2. **Eliminate Silent 1.0 Parity in `src/mdrap/fx.py` (F-QNT-04)**:
   - Raise `ValueError(f"Unmapped currency: {currency}")` instead of returning 1.0.

## Priority 1 (Microstructure & Risk)
3. **Fix `run_walkforward` in `src/mdrap/backtest.py` (F-QNT-02)**:
   - Pass training data to strategy fitting routine or document that this is sliding testing only.
4. **Fix `VolatilityTracker` Sliding Window in `src/mdrap/analytics.py` (F-QNT-05)**:
   - Implement true sliding window or rename to `ExpandingVolatilityTracker`.
5. **Bridge Pre-Trade Risk to Core Engine (F-QNT-03)**:
   - Expose active order validation interface in core.
"""
    },
    "adversarial": {
        "title": "Agent 9: Independent Adversarial Reviewer",
        "findings": """# Independent Adversarial Reviewer — Detailed Findings

## Executive Summary
Audited all public claims against ground-truth source code, benchmark outputs, and operational telemetry. While MDRAP contains genuine, high-quality C and Python market data processing algorithms, the repository exhibits significant over-claiming, benchmark data inflation, version dissociation, committed platform-specific binary blobs, mock-heavy test inflation, and a 32-second "24-hour soak test".

## Categorized Findings

### CRITICAL
1. **F-ADV-01: Version Dissociation (4.0.1 Codebase vs 5.3.0 Claims)**:
   - Package is hardcoded to `4.0.1` across `_version.py`, `pyproject.toml`, `/health` REST endpoint, and `claims_manifest.json`, while documentation touts `v5.3.0`.
   - Users installing from PyPI or git receive version `4.0.1`.

2. **F-ADV-02: Benchmark Reproducibility & Fabrication Failures**:
   - `README.md` claims `python benchmarks/bench_scale.py` reproduces the 43.67M EPS Native C kernel. It actually runs a Python `Engine.submit()` benchmark (~20k–50k EPS).
   - In `benchmarks/stage_breakdown.json:49-56`, p50, p95, p99, and max for `native_c_kernel` are all identical (22.89 ns). The 45.0 ns p99 in `README.md` was manually fabricated.
   - Raw committed benchmark data in `audit/v5.3/raw/benchmarks_raw.json` records Native C at **416.3k EPS** and Python at **149.9k EPS** (2.78x speedup). `README.md` inflates these to **510.8k EPS** and **170.1k EPS** (3.00x).

3. **F-ADV-03: Committed Platform-Specific Binaries in Git**:
   - `src/mdrap/_fastpath_native.dll` and `src/mdrap/mdrap-core.exe` are committed directly to the repository, posing supply-chain security risks.

### HIGH
4. **F-ADV-04: The 32.71-Second "24-Hour Continuous Soak Test"**:
   - `docs/audit/v5/24H_SOAK_TEST_REPORT.md:16-19` openly notes the "24-hour soak" ran for only **32.71 seconds** (1,032 events total, 1 event for Kraken), with 24-hour stability mathematically extrapolated.

5. **F-ADV-05: Aspirational Blueprints Labeled as Production Features**:
   - `af_xdp.py` contains no kernel bypass; it is an in-memory `bytearray` simulator.
   - `multicast_arbitrator.py` contains no multicast UDP sockets; it is an in-memory queue simulator.
   - `consensus.py` header notes it is an in-process simulation not for multi-host consensus.
   - `kafka_sink.py` contains no Kafka driver dependencies and defaults to `InMemoryKafkaProducer`.

6. **F-ADV-06: Disk Durability "Always" Claim Overstated**:
   - `README.md` advertises 21,615 EPS for "Always" durability. In reality, true tick-by-tick synchronous fsync achieves only 270 EPS. The 21.6k figure reflects batch-100 writes.

### MEDIUM
7. **F-ADV-07: Core Edition Boundaries Blurred**:
   - Matching Engine, Avellaneda-Stoikov MM, and 14 Risk Gates are advertised in `README.md` as core capabilities, but reside in `packages/mdrap-strategies`.
8. **F-ADV-08: Dead Code in Core**:
   - 779 lines of dead code in `trading_cli.py`, legacy `BinaryJournal` in `journal.py`, and `scheduler.py` are unreferenced or superseded.
9. **F-ADV-09: Hardcoded Demo Credentials in Load Simulator**:
   - `src/mdrap/workload_simulator.py:49` hardcodes `auth_token: str = "mdrap_demo_pro_key"`.
""",
        "evidence": """# Independent Adversarial Reviewer — Exact Evidence

## 1. Soak Test Duration
- File: `docs/audit/v5/24H_SOAK_TEST_REPORT.md:16-19`
  ```markdown
  Audit Status: SUSTAINED MULTI-VENUE SOAK EXECUTED (24-HOUR CONTINUOUS RUN INCOMPLETE DUE TO AGENTIC SESSION TIMEOUT BOUNDARIES).
  An empirical sustained live soak run of 32.71 seconds was executed...
  A total of 1,032 genuine market events were ingested.
  ```

## 2. Fabricated Percentiles in Stage Breakdown
- File: `benchmarks/stage_breakdown.json:49-56`
  ```json
  "native_c_kernel": {
    "total_events": 1000000,
    "p50_ns": 22.896999944350682,
    "p95_ns": 22.896999944350682,
    "p99_ns": 22.896999944350682,
    "max_ns": 22.896999944350682
  }
  ```

## 3. Committed Binaries
- Files: `src/mdrap/_fastpath_native.dll` and `src/mdrap/mdrap-core.exe` tracked in Git.

## 4. Hardcoded Demo Token
- File: `src/mdrap/workload_simulator.py:49`
  ```python
  auth_token: str = "mdrap_demo_pro_key"
  ```
""",
        "recommendations": """# Independent Adversarial Reviewer — Recommendations

## Priority 0 (Release Blockers)
1. **Unify Canonical Version to 5.3.0 (F-ADV-01)**.
2. **Purge Committed Binaries from Git (F-ADV-03)**:
   - Remove `_fastpath_native.dll` and `mdrap-core.exe` from Git tracking and add to `.gitignore`.
3. **Synchronize README Benchmarks with Raw Evidence (F-ADV-02)**:
   - Correct Native C throughput to 416.3k EPS, Python to 149.9k EPS, and speedup to 2.78x.
   - Correct reproduction command for isolated C kernel.

## Priority 1 (Accurate Documentation)
4. **Retitle Soak Test Report (F-ADV-04)**:
   - Retitle `24H_SOAK_TEST_REPORT.md` to `BURST_SOAK_AND_STABILITY_REPORT.md` and explicitly label 24-hour figures as mathematical projections.
5. **Clarify Edition Boundaries in README (F-ADV-07)**:
   - Clearly delineate core features vs features requiring `packages/mdrap-strategies`.
6. **Label Blueprint Modules Explicitly (F-ADV-05)**:
   - Document `af_xdp.py` and `multicast_arbitrator.py` as simulation harnesses.

## Priority 2 (Code Hygiene)
7. **Delete Dead Modules (F-ADV-08)**:
   - Remove `trading_cli.py`, `shm_drainer.py`, `scheduler.py`, `journal.py`.
8. **Remove Hardcoded Demo Token (F-ADV-09)**.
"""
    }
}

def main():
    for area, data in AGENTS.items():
        area_dir = BASE / area
        area_dir.mkdir(parents=True, exist_ok=True)
        
        findings_path = area_dir / "FINDINGS.md"
        findings_path.write_text(data["findings"].strip() + "\\n", encoding="utf-8")
        
        evidence_path = area_dir / "EVIDENCE.md"
        evidence_path.write_text(data["evidence"].strip() + "\\n", encoding="utf-8")
        
        recs_path = area_dir / "RECOMMENDATIONS.md"
        recs_path.write_text(data["recommendations"].strip() + "\\n", encoding="utf-8")
        
        print(f"Materialized {area}: FINDINGS.md, EVIDENCE.md, RECOMMENDATIONS.md")

if __name__ == "__main__":
    main()
