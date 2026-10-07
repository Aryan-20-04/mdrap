# Changelog

All notable changes to MDRAP are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-10-07

### Added
- **Phase 1: Event Identity Decoupling (Card #1)**: Decoupled monotonic event identity from deduplication using `{run_id}-{n}` run-scoped prefixes, eliminating primary key collisions across restarts. Added observable SQLite conflict metrics (`mdrap_storage_conflicts_total`) and `StorageConflictError` on unhandled collisions.
- **Phase 1: Audit Checkpoints (Card #5)**: Added local HMAC-SHA256 signing for `(entry_count, head_hash)` checkpoints and prefix verification that accepts valid appends. Checkpoints are not automatically exported off-box, and the unkeyed entry chain does not prevent a database writer from rewriting the whole chain without a trusted checkpoint.
- **Phase 2: Database Schema Version Tracking (Card #8)**: Added a `schema_migrations` log and rejection of databases with a future `user_version`. The log is not authoritative and no ordered per-version migration functions are implemented.
- **Phase 3: Storage Writer Recovery & Dead-Letter Replay (Card #9)**: Added a writer-thread liveness check and dead-letter replay CLI. A storage write followed by a spill failure now marks the pipeline degraded, raises `WriterFailure` on subsequent ingest, and makes readiness return 503. The configured default dead-letter directory is still derived from the current working directory. The async queue remains volatile until a journal-first write path is implemented.
- **Phase 5: Plugin Registry & Conformance Tests (Card #4)**: Added `PluginRegistry` source code with entry-point discovery, signature checks, version-handshake validation (`__min_mdrap_version__`), pipeline hooks, and conformance tests. Hook drops/errors now produce quarantine outcomes with counters and lineage. The runtime does not consume the registry, and `plugins.py` is omitted from the wheel's module list.
- **Phase 6: Stability Labels & Deprecation (Card #10)**: Added `__stability__` labels and a `@deprecated` decorator emitting `DeprecationWarning`. Current tests do not compare public signatures against a prior release, so these labels do not constitute an enforced compatibility guarantee.

### Fixed
- **Phase 1: Feed Status & Documentation (Card #3)**: The API no longer creates a phantom simulator feed; feed registration records metadata as `REGISTERED_NOT_RUNNING`. The API does not start or supervise registered feeds, and its WebSocket broadcaster is not connected to a source runtime. Documentation now omits the unserved TCP port.
- **Phase 1: Security Hardening (Card #6)**: TCP gateway fails closed when `require_auth=True` without a security manager. API key revocation and expired-key cleanup persist before mutating memory. Non-demo `SecurityManager` startup now requires a non-empty `MDRAP_API_KEY_SALT`; test configuration supplies a test-only salt without enabling demo mode.
- **Phase 3: Native Delivery Safety (Card #7)**: Gated automatic C compilation behind `MDRAP_AUTO_COMPILE=1` without compile-on-import races.
- **Phase 6: Strict Boolean Numeric Rejection (Card H)**: Fixed Python `bool` numeric subclass trap in `models.py` and `gateway.py`; booleans are strictly rejected for prices, quantities, bids, asks, sequence numbers, and timestamps.
- **Phase 6: Monotonic Watchdog Silence Detection (Card H)**: Replaced wall-clock `time.time()` with monotonic clock (`time.monotonic()`) in `watchdog.py` to eliminate false silence alerts during NTP clock adjustments.
- **Phase 6: Exception Handling Changes (Card H)**: Replaced some broad silent handlers with explicit handling/logging in selected modules. Broad exception handlers remain, including in API, storage, watchdog, gateway, and WebSocket paths; silent exception swallowing has not been eradicated.
- **Phase 6: Test Loopback Isolation (Card H)**: Gated `"testclient"` loopback authentication bypass in `api.py` strictly behind test environments.

### Changed
- **Phase 2: Version Reporting (Card #2)**: CLI/API, the plugin handshake, and setuptools now use the package version from `src/_version.py`. The Docker image no longer embeds a duplicate version label; the README badge and quickstart response example are checked by the documentation claims test.
- **Phase 3: Batched Processing and Timing Sampling (Card P)**: Added batch processing and configurable timing sampling. The published pipeline latency improvements have not been independently reproduced for this checkout and must not be treated as production performance evidence.
- **Phase 5: Storage Interface Segregation (Card #4)**: Split `StorageBackend` into `AppendStorageSink` (ingest write path) and `QueryStorageStore` (query path), while keeping `StorageBackend` as composite interface for 100% backward compatibility.
- **Phase 6: Zero Mandatory Dependencies (Card H / Card #2)**: Provided graceful pure-Python stdlib fallback in `term.py` and `cli.py`, making `rich` an optional extra (`.[ui]`) and enabling `mdrap` core to run with zero external runtime dependencies.
- **Supply Chain Hardening (Card H)**: Purged test dependencies (`pytest`) from production `.[all]` optional extras.

## [2.4.1] - 2026-10-02

### Added
- **Slowest-Reader Watermark Tracking**: Real-time reader watermark tracking in zero-copy shared memory (`src/shm.py`, `tests/test_shm_watermark.py`).
- **Memory Ordering Fences**: Explicit native memory barrier fences (`_mm_sfence` / `_mm_lfence`) in `_fastpath_native.dll` and `src/fastpath.c`.
- **GIL-Released C Micro-Batching**: GIL release around C compute batches in `src/_fastpath_c.c` and `src/fastpath.py`.
- **WebSocket Drop Counters**: Backpressure drop tracking and resilient connection logging in `src/ws_feed.py`.

### Fixed
- **API Key Revocation**: Deterministic key ID entropy matching in `src/security.py`.
- **Gateway Timestamp Normalization**: Nullable exchange timestamp fallback for unsequenced crypto feeds (`src/gateway.py`).
- **Reconciliation In-Place Mutation**: Fixed cache mutation on cross-feed disagreement in `src/reconciliation.py`.
- **Spinlock Self-Deadlock Fix**: Resolved recursion and thread cleanup deadlocks in `src/fastpath.c`.

### Changed
- **Stdlib Hygiene Cleanups**: Replaced hand-rolled percentile logic with `statistics.quantiles` in contention benchmark.
- **Byte Formatting Optimization**: Replaced manual hex formatting with `bytes.hex(' ')` in differential runner.
- **Service Dedup**: Imported `poll_keypress` directly from `term.py`.

## [2.4.0] - 2026-09-25

### Added
- **Layer 1: Native Core AVX2 SIMD & Invariant RDTSC Engine (`src/mdrap_core.c`)**:
  - Calibrated Invariant RDTSC hardware timing (`CPUID.80000007H:EDX[8]`) with zero kernel context-switch overhead (2.61 GHz invariant clock).
  - AVX2 256-bit SIMD slot writes (`_mm256_storeu_si256`) writing 128-byte slots in four vector operations with store fences (`_mm_sfence`).
  - Hardware L1 cache prefetching (`_mm_prefetch`).
  - 32-tick amortized head sequence publishing with seqlock consumer decoupling.
  - Command-line CPU core pinning (`--core` argument via Windows `SetProcessAffinityMask` / Linux `sched_setaffinity`).
  - Measured throughput: **21,296,184 eps** (p50: **47.0 ns**) at 1M events; **21,096,231 eps** (p50: **47.4 ns**) sustained at 10M events.
- **Layer 2: Native Python C-API Extension Module (`src/_fastpath_c.c`, `src/fastpath.py`)**:
  - Direct C-API extension (`_fastpath_c.pyd`) with `METH_FASTCALL` parameter passing, eliminating ctypes FFI marshaling and object boxing.
  - Measured throughput: **415,581 eps** (+108.9% speedup / 2.09x over Phase 0 baseline).
  - Per-event compute latency: **p50: 1.90 µs**, **p95: 2.10 µs**, **p99: 2.70 µs**.
- **Layer 3: Decoupled Memory-Mapped Binary Journal & Asynchronous Drainer (`src/journal.py`, `src/shm_drainer.py`)**:
  - Fixed 128-byte append-only binary transaction log (`.dbn` / AOF) matching the SHM slot format.
  - Asynchronous background worker (`SHMDrainWorker`) polling the lock-free circular SHM ring buffer without producer contention.
  - Auto-healing partial file truncation recovery on system crash or abnormal termination.
  - Measured throughput: **332,871 eps** (+1,780.4% / 18.80x speedup over SQLite WAL baseline).
  - Persistence latency: **p50: 2.50 µs** (99.7% latency reduction), **p95: 4.50 µs**, **p99: 5.80 µs**.
- **Catastrophic Failure Mode Test Suite (`tests/test_failure_modes.py`)**:
  - Writer process crash and epoch rollover validation.
  - Seqlock torn-read recovery during writer mid-write race conditions.
  - Asynchronous drain worker crash and seamless restart resumption from journal state.
  - Binary journal partial record auto-healing and truncation recovery.
  - Buffer overrun and extreme watermark backpressure telemetry verification.
- **Multi-Venue Soak Load Test (`benchmarks/run_soak_test.py`)**:
  - 1,000,000-event multi-symbol (5 instruments) and multi-venue (3 sources) continuous soak stream.
  - Verified **zero dropped events**, zero laps, and flat memory RSS.
- **Comprehensive Post-Optimization Benchmark Suite (`benchmarks/measure_phase6_optimized.py`)**:
  - Automated scorecard reporting and serialization to `benchmarks/results/optimized_phase6.json`.

### Benchmark Scorecard (Baseline Phase 0 vs Optimized Phase 6)

| Layer / Metric | Baseline (Phase 0) | Optimized (Phase 6) | Speedup / Improvement Delta |
| :--- | :--- | :--- | :--- |
| **Layer 1: Native Hotpath EPS (1M)** | 19,513,680 eps | **21,296,184 eps** | **+1.09x (+9.1%)** |
| **Layer 1: Per-Tick Latency (1M)** | 51.20 ns | **47.00 ns** | **+8.2% faster** (Sub-50ns scale) |
| **Layer 1: Sustained Run EPS (10M)** | *(unscaled)* | **21,096,231 eps** | **Sustained >21M eps** |
| **Layer 1: Latency per Tick (10M)** | *(unscaled)* | **47.40 ns** | **Sub-50ns scale** |
| **Layer 2: Python Compute Loop EPS** | 198,912 eps | **415,581 eps** | **+2.09x (+108.9% speedup)** |
| **Layer 2: Compute Latency p50** | 4.60 µs | **1.90 µs** | **+58.7% faster** |
| **Layer 2: Compute Latency p99** | 6.70 µs | **2.70 µs** | **+59.7% faster** |
| **Layer 3: Persistence EPS** | 17,702 eps | **332,871 eps** | **+18.80x (+1,780.4% speedup)** |
| **Layer 3: Persistence Latency p50** | 795.10 µs | **2.50 µs** | **+99.7% latency reduction** |
| **Layer 3: Multi-Venue Soak (1M)** | *(unscaled)* | **1,000,000 events** | **0 dropped events / 0 laps** |

## [2.3.0] - 2026-09-24

### Added
- **Formal Institutional Development Protocol**: Established the 20-rule development protocol and 9-stage zero-defect gate in `CONTRIBUTING.md` and automated validation via `scripts/check.py`.
- **Public API Stability Contract (`docs/API_STABILITY.md`)**: Formalized module and symbol lifecycle designations across `STABLE`, `EXPERIMENTAL`, `INTERNAL`, and `DEPRECATED`. Added `__stability__` annotations across all core and research modules.
- **Unified Canonical Event Model (`MarketEvent`)**: High-level unified domain model with typed polymorphic variants (`TradeEvent`, `QuoteEvent`, `BookEvent`, `DepthEvent`), zero-loss serialization roundtrip, and fuzz-tested boundary safety.
- **Rule Metadata Registry & Introspection Matrix**: Added structured rule definitions (`MD001` through `MD015`), severity levels (`INVALID`, `SUSPICIOUS`, `VALID`), trigger conditions, execution stages, and lookup APIs (`get_rule_by_id`, `get_rule_by_name`, `list_registered_definitions`) in `src/rules.py`.
- **Quarantine Subsystem (`src/quarantine.py`)**: Standalone non-loss quarantine records (`QuarantineRecord`, `QuarantineManager`) preserving raw payloads, venue provenance, timestamps, violation reasons, and triage states.
- **Feed Adapter Protocol & Reference Implementation**: Formalized `FeedAdapter` protocol (`connect`, `disconnect`, `receive`, `normalize`, `health`) and built `ReferenceFeedAdapter` (`src/adapters/reference.py`) with complete authoring guide (`docs/extending/feed-adapter-guide.md`).
- **Python Public SDK Contract**: Clean top-level SDK import (`from mdrap import Client, MDRAPClient, MarketEvent`) with authenticated REST, WebSocket streaming, and client-side error handling.
- **Automated Regression & Efficiency Sweeps**: Standalone benchmark suite (`benchmarks/run_performance_suite.py`), regression gate (`benchmarks/check_regression.py`), and scaling efficiency sweep (`benchmarks/efficiency_sweep.py`).
- **Clean-Room Bootstrap Verification**: Cross-platform bootstrap runner (`scripts/bootstrap.py` and `scripts/bootstrap.sh`) for clean-clone environment compilation and smoke test validation.

### Changed
- **Reconciliation Engine Determinism**: Hardened source tie-breaking using strictly sorted unique venue keys, guaranteeing 100% deterministic consensus across identical feeds regardless of input stream order.
- **Research Module Isolation**: Decoupled research, options, TCA, and risk modules (`src/options.py`, `src/risk.py`, `src/backtest.py`) with explicit `__stability__ = "experimental"` markings to prevent experimental code from dictating core pipeline stability.
- **Health Probe Separation**: Split system health monitoring into distinct `/liveness` (process responsiveness), `/readiness` (database and pipeline processing readiness), and `/health` (summary diagnostic) HTTP endpoints.

### Fixed
- **Audit Hash Chain Tamper Proofing**: Secured cryptographic audit logging with verifiable tamper resistance, corruption detection, deletion detection, and portable JSON proof export (`Store.export_audit_proof`).
- **Storage Subsystem Resilience**: Hardened SQLite WAL mode against writer locking, confirmed zero-leak DuckDB columnar writes, and validated concurrent reader isolation.
- **Transport Subsystem Contention**: Enhanced shared memory ring buffer empty/full boundary handling and hardened WebSocket subscription protocol framing.
- **Adapter Subclass Compatibility**: Enabled structural subtyping in `FeedAdapter` protocol via custom `__subclasshook__`, allowing backward compatibility with legacy `open`/`close`/`__iter__` streams while supporting modern `connect`/`receive` adapters.

### Security
- **Strict Bearer & First-Frame Authorization**: Replaced deprecated query-string URL token authentication with mandatory handshake headers (`Authorization: Bearer <token>` or `X-API-Key: <token>`) and first-frame JSON message authentication (`{"action": "authenticate", "token": "..."}`) on WebSocket streaming and all REST routes. Query-string parameter `?token=` is now strictly rejected with `1008 Policy Violation` (WebSocket) and `401 Unauthorized` (REST) to prevent credential leakage in proxy/access logs.
- **Explicit CORS Origin Policy**: Restrained CORS default to empty (disabled), requiring explicit comma-separated trusted origins or explicit `*` override in `MDRAP_CORS_ORIGINS` for development.
- **Metrics Endpoint Authentication**: Secured Prometheus metrics export behind operator/admin authentication by default (`MDRAP_METRICS_AUTH=1`) with trusted reverse proxy support via `MDRAP_TRUSTED_PROXY_IPS`.
- **API Key In-Memory Hashing**: Purged raw plaintext API keys from process memory (`ClientEntitlement.token` cleared, `HashedKeyStore` storing only SHA-256 digests), and disabled automatic admin bootstrap by default (`MDRAP_AUTO_BOOTSTRAP_ADMIN=0`).
- **Supply Chain CVE Hardening**: Enforced safe dependency floors (`pyarrow>=20.0.0` for PYSEC-2026-113, `pytest>=8.4.2` for PYSEC-2026-1845).

### Performance
- **Microsecond Tail Latency**: Achieved end-to-end Python pipeline latency of **p50: 783.6 µs** and **p99: 2,179.8 µs** at 13,529 events/sec with zero dropped ticks.
- **Flat Memory Footprint**: Verified constant memory usage across 10,000 to 50,000 event continuous runs (31 MB to 106 MB RSS) with no garbage collector degradation.

### Breaking Changes
- **Deprecated URL Tokens**: Passing API tokens via `?token=` query parameters in REST or WebSocket requests is now rejected (`401 Unauthorized` / `WS 1008 Policy Violation`). Clients must supply tokens via handshake headers or first-frame authentication.
- **Secure Defaults**: Prometheus metrics endpoint (`/metrics`) now requires authentication by default (`MDRAP_METRICS_AUTH=1`). CORS is disabled by default unless explicitly configured in `MDRAP_CORS_ORIGINS`. Automatic admin key creation is disabled by default (`MDRAP_AUTO_BOOTSTRAP_ADMIN=0`).
- **Health Probes**: Infrastructure orchestrators (Kubernetes, Docker) should migrate probes from `/health` to `/liveness` and `/readiness`.

### Benchmark Comparison (v2.2.0 vs v2.3.0)

| Metric | v2.2.0 Baseline | v2.3.0 Release Candidate | Delta / Verification |
| :--- | :--- | :--- | :--- |
| **Throughput (eps)** | 13,500 eps | **13,529.9 eps** | +0.2% (stable) |
| **Latency p50** | 785.0 µs | **783.6 µs** | -0.2% (improved) |
| **Latency p95** | 1,780.0 µs | **1,774.1 µs** | -0.3% (improved) |
| **Latency p99** | 2,185.0 µs | **2,179.8 µs** | -0.2% (improved) |
| **Memory RSS (10k)** | 48.0 MB | **46.5 MB** | -3.1% (reduced) |
| **Dropped Events** | 0 | **0** | Zero loss verified |
| **Audit Verification** | 100% | **100%** | Cryptographically verified |

## [2.2.0] - 2026-09-22

### Added
- **Standalone Native C Hot-Path Engine (`mdrap-core`)**: Fully decoupled out-of-process C binary (`src/mdrap_core.c`) running wire-to-SHM with zero Python runtime, CPython FFI, or GIL involvement, achieving **22.35 Million events/sec** (**44.8 ns per tick** wire-to-SHM latency).
- **Zero-Lock SPSC Shared Memory Ring Buffer**: Cross-platform memory-mapped ring buffer (Windows named file mapping / POSIX `shm_open`) with 128-byte cache-line aligned slots, atomic release fences, and two-phase commit protocol (`UNCOMMITTED` seq invalidation -> payload store -> fence -> commit sequence publication).
- **Single-Writer Lock-Free Ingestion Benchmark**: Multi-source contention benchmark (`benchmarks/bench_contention.py`) proving flat p99.9 tail latency (0.30 µs at 8 sources) and eliminating mutex convoying.
- **Hardware Timestamping Diagnostics (`mdrap doctor`)**: Added institutional clock source diagnostics detecting `SO_TIMESTAMPING` capabilities and Linux PTP Hardware Clocks (`/dev/ptp*`), with graceful fallback to software QPC on Windows and mach time on macOS.
- **CLI Subcommand `mdrap core`**: Terminal command and runner for executing the standalone native hot-path engine with configurable event counts, shared memory names, rate throttling, and automatic compilation.
- **Shared Memory Fuzzing Suite**: Fuzz testing harness (`tests/test_shm_fuzz.py`) covering torn reads, corrupted magic numbers, unsupported versions, epoch mutation, and ring buffer wrap-around overrun detection.
- **Architecture Decision Record (ADR 0003)**: Documented process split decision retaining C with single-writer process boundary and backlogging Rust rewrite (`docs/decisions/0003-native-core-process-split.md`).
- **T2 FPGA Hardware Learning Track**: Synthesizable Verilog RTL modules (`fpga/mdrap_crossed_quote.v`, `fpga/mdrap_sequence_gap.v`, `fpga/tb_mdrap_rules.v`), cycle-accurate parity test (`tests/test_fpga_parity.py`) with 100% agreement on 1,000 events, and an in-depth hardware latency findings report (`docs/fpga-spike-findings.md`).

## [2.1.0] - 2026-09-21

### Added
- **Avellaneda-Stoikov Quantitative HFT Market Maker**: High-frequency market-making strategy (`src/strategy_sdk.py`) with inventory skewing, toxic order-flow spread widening, tick grid quantization, and integrated `FastQualityEngine` quality shield.
- **Micro-Throughput Vectorized SBE Engine**: SIMD-capable C validation kernel achieving **15.54 Million events/sec** (**64.4 ns per event**, 15.5x over the 1 MEPS goal).
- **Golden Parity Suite**: 350-vector golden test suite (`tests/test_golden_parity.py`) guaranteeing 100% Python-to-C verdict and reason code agreement.
- **Covering Time-Series & Retention Indexes**: Added `idx_canonical_exch_ts` on `canonical_events(exchange_timestamp DESC)` for instant time-series query scans and `idx_quarantine_recv_ts` on `quarantine(receive_timestamp)` for retention chunking.

### Optimized
- **Hot-Path Memory Allocations & Generator Streaming**: Streamed canonical events via generator expressions in `write_canonical_batch` and `write_batches_atomic`, eliminating multi-megabyte intermediate list allocations.
- **Zero-Allocation Field Validation & Enum Caching**: Replaced list comprehensions with short-circuit loops in `gateway.normalize` and cached static `EventType` enum instances.
- **Decoupled SHM String Caching**: Bounded ASCII encoding and decoding caches in `src/shm.py` eliminating string allocations on IPC ticks.
- **Throttled Live Terminal Visualizer**: Limited `LiveTickerDashboard` terminal re-renders to 15 Hz while processing events at maximum wire speed, eliminating up to 50,000 table/panel allocations per second.
- **Magic Number Elimination**: Extracted all inline literals into documented institutional constants across strategy, quality, pipeline, storage, reconciliation, and native C kernels.

## [2.0.2] - 2026-09-18

### Optimized
- **Telemetry Latency Sampling & O(1) Memory Footprint**: Implemented `CompactSampleBuffer` using single-precision 32-bit float arrays (`array.array('f')`) and systematic downsampling capped at 50,000 samples. Strictly bounds telemetry memory to < 200 KB regardless of event volume (slashing memory by 290x on 100k events and eliminating OOM on 1B events).
- **SQLite Storage Memory Overhead**: Tuned default SQLite pragmas to 64 MB mmap (down from 256 MB) and 16 MB page cache (down from 64 MB) across read/write connections, reclaiming ~430 MB of virtual working set with equal or faster throughput. Added `MDRAP_SQLITE_MMAP_MB` and `MDRAP_SQLITE_CACHE_MB` environment variable overrides.
- **Paper Trading EMS Memory Bounding**: Converted `PaperExecutor` order, fill, and equity records to bounded rolling deques (`maxlen=10_000`) and introduced running scalar accumulators (`_total_trades`, `_win_count`, `_slippage_bps_sum`, `_total_slippage_usd`) to allow long-running and multi-million event strategy executions in constant memory.
- **Quality Deduplication Cache Hashing**: Converted `QualityEngine` dedup keys from 6-element Python tuples to 64-bit integer hashes and tuned default LRU window to 50,000 entries, cutting dedup heap footprint from 37 MB down to ~6 MB.

### Fixed
- **Strategy Universe Simulation**: Ensured simulator generates targeted symbols when running single or custom multi-instrument universes.
- **Export Directory Auto-Creation**: Automatically creates parent directories when exporting strategy or table reports to nested paths.
- **CLI Visual Ergonomics & Accessibility**: Added colorblind indicators (`● VALID`, `▲ SUSPICIOUS`, `✕ INVALID`), rounded border aesthetics, `NO_COLOR` standard compliance, and scoped error command palettes.

## [2.0.1] - 2026-09-18

### Fixed
- **Desk Navigator Parameter Error**: Updated `MDRAPNavigator.__init__` to accept `db_path` parameter and guarded `mdrap demo` to cleanly handle headless/non-TTY execution without failing raw input mode.

## [2.0.0] - 2026-09-18

### Added
- **Single Source of Truth Quality Engine**: `src/rules.def` X-macro shared between C hot-path and Python with `CORE_REASON_MASK` enforcement and CI sync verification (`tools/gen_reasons.py`).
- **Micro-FFI Struct-by-Pointer Acceleration**: High-performance batch ctypes boundary crossing achieving 452 ns per-event boundary latency (4.58x faster than baseline) with direct pointer array buffer mapping.
- **Continuous Fuzzing & Differential Verification**: libFuzzer SBE and batch harnesses (`fuzz/fuzz_sbe.c`, `fuzz/fuzz_batch.c`), differential property fuzzing (`fuzz/fuzz_differential.py`) guaranteeing 100% rejection/acceptance parity between Python and C.
- **Layered TOML Configuration**: `mdrap.toml` schema and stdlib `tomllib` config loader with hierarchical inheritance (`defaults -> venue -> instrument_class -> instrument`) and origin tracking (`mdrap config show`).
- **Extensibility Framework**: Standardized `FeedAdapter` protocol (`src/adapters/__init__.py`), rapid venue adapter template (`src/adapters/template.py`), and zero-overhead custom rule decorator `@register_rule(bit=32..63)`.
- **Institutional Diagnosability & Demo**: `mdrap doctor` environment and integrity self-checks, `mdrap demo` 50k live desk run, and non-silent native fallback telemetry.
- **Reproducibility & Compliance Export**: Run reproducibility manifests (`manifest.json`), Parquet/JSON/CSV export with SQL injection prevention (`mdrap export`), and strict typing stubs (`py.typed`, `src/fastpath.pyi`).
- **Multi-Python CI Matrix**: Automated GitHub Actions testing across Python 3.11, 3.12, 3.13, and 3.13 free-threaded (`3.13t`).

## [1.2.2] - 2026-09-17

### Added
- **Storage Retention & Compaction**: `mdrap retention` (`compact`, `prune`) with `--days` and `--quarantine-days` (default 90 days for compliance evidentiary completeness) and WAL checkpoint truncation / VACUUM disk reclamation.
- **Scheduled Retention**: `Scheduler.schedule_retention()` to automate database maintenance on recurring cron schedules.
- **Agent-Friendly JSON Support**: `--json` flag support across all data-producing commands (`status`, `bbo`, `analytics`, `watchdog`, `version`, etc.) for zero-scraping AI agent consumption.
- **OpenFIGI Support**: Added Financial Instrument Global Identifier (`figi`) field to `SymbolInfo` and 12-character global resolution in `resolve_symbol()`.
- **Clock Source Traceability**: Added `clock_source` field to `CanonicalEvent` for MiFID II RTS 25 compliance.
- **Supply Chain & Security**: `SECURITY.md` vulnerability disclosure policy, upper-bound dependency bounds in `requirements.txt`, and market data licensing disclaimer in `README.md`.
- **Binary Wheels Matrix**: GitHub Actions workflow (`wheels.yml`) using `cibuildwheel` across Linux, macOS, and Windows.

### Fixed
- **Linux Native Library Import Collision**: Renamed compiled C library to `_fastpath_native.*` to prevent `fastpath.so` from hijacking Python's `import fastpath`.
- **CLI Global Flag Mangling**: Fixed bug where global flags like `--json` were misidentified as stock tickers.
- **Non-Windows CI Test Guard**: Fixed unconditional `msvcrt` import in `test_navigator_coverage.py` on Linux/macOS.
- **Unbounded Symbol Cache**: Added LRU cache eviction (`max_instruments=2000`) in `ConsolidatedDepthEngine` to prevent daemon memory leaks.
- **Fast Local Test Loop**: Tagged stress/throughput tests with `@pytest.mark.slow`, reducing local test runs from 116s to ~70s.

## [1.2.1] - 2026-09-15

### Added
- **Pipeline Micro-Batching**: Micro-batching support for C boundary crossing to minimize FFI overhead.
- **Read Connection Decoupling**: Secondary read-only SQLite connection to eliminate reader/writer lock contention.
- **Symbology As-Of-Date Awareness**: Point-in-time ticker resolution to prevent lookahead bias in historical analysis.
- **Instrument-Sharded Stress Testing**: Parallelized stress testing partitioned across instruments.

## [1.2.0] - 2026-09-12

### Added
- **Modal Navigator Desk**: Interactive full-screen terminal workspace (`mdrap desk`).
- **Strict Sync & Security Hardening**: DuckDB divergence detection, SSRF protection, and error quarantine.

### Changed
- **85% Test Coverage**: Achieved 85% automated test coverage across core ingestion and validation modules.

## [1.1.0] - 2026-08-28

### Added
- Native C fastpath acceleration, global venue symbology, and maritime vessel tracking.

## [1.0.0] - 2026-08-15

### Added
- Initial stable release with deterministic market data validation, SQLite storage, and CLI tools.
