# Changelog

All notable changes to MDRAP are documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
