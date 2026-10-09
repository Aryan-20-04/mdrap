# MDRAP Phase 7 — Dependency Lifecycle & Third-Party Package Inventory

## 1. Executive Summary & Inventory Scope
MDRAP strictly limits third-party software dependencies. The core market data engine, normalization pipeline, quality evaluator, and persistence drainer have **zero external third-party library dependencies** beyond the standard Python 3.13 runtime and standard C99 compiler library.

---

## 2. Definitive Package Inventory

### 1. rich
- **Pinned Version**: `13.9.4`
- **License**: MIT
- **Scope & Role**: Console rendering, CLI status tables, progress monitors (`src/cli.py`).
- **Direct / Transitive**: Direct dependency.
- **Maintenance Status**: Actively maintained by Textualize.
- **Security Exposure**: Low (strictly isolated to terminal output rendering).
- **Replacement Option**: Fallback to standard Python `print()` and `sys.stdout` if removed.

### 2. fastapi & uvicorn (Optional)
- **Pinned Versions**: `fastapi == 0.115.0`, `uvicorn == 0.30.6`
- **License**: MIT / BSD-3-Clause
- **Scope & Role**: Optional REST & WebSocket management daemon (`src/api.py`).
- **Direct / Transitive**: Direct dependency.
- **Maintenance Status**: Industry-standard ASGI framework.
- **Security Exposure**: Medium (network-facing HTTP/WS endpoints). Mitigated via PBKDF2 authentication and rate limiting.
- **Replacement Option**: Raw Python `asyncio` socket daemon.

### 3. zstandard (Optional)
- **Pinned Version**: `0.23.0`
- **License**: BSD-3-Clause
- **Scope & Role**: Level-19 cold archive compaction (`src/archive.py`).
- **Direct / Transitive**: Direct dependency.
- **Maintenance Status**: Actively maintained Meta Zstd bindings.
- **Security Exposure**: Low (batch file compression during EOD).
- **Replacement Option**: Standard library `gzip` (lower compression ratio).

### 4. pytest (Development / Test Only)
- **Pinned Version**: `8.3.4`
- **License**: MIT
- **Scope & Role**: Automated test execution across 1,267 test cases.
- **Direct / Transitive**: Direct dev-dependency (excluded from production builds).
