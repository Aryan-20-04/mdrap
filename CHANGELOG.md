# Changelog

All notable changes to MDRAP are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
