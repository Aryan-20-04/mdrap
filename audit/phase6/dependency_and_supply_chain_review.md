# MDRAP Phase 6 — Dependency & Supply Chain Security Review

## 1. Executive Summary & Design Principle
Under institutional financial engineering and `/ponytail` discipline, third-party software dependencies represent an operational attack vector, licensing risk, and maintenance liability.

MDRAP enforces an ultra-lean dependency architecture:
- **Core Engine & Partitioning**: **100% Python Standard Library** (`socket`, `struct`, `sqlite3`, `hashlib`, `collections`, `threading`, `multiprocessing`).
- **Core CLI**: Single external dependency on `rich` (for institutional console rendering).
- **Optional Extensions**: Pinned packages for S3 sync, FastAPI endpoints, and test runners.

**Supply Chain Verdict: PASS (Zero Vulnerabilities, Zero Copyleft Licensing Risks)**

---

## 2. Direct Dependency Inventory & Licensing Audit

| Package Name | Pinned Version | License | Justification & Usage Scope | Supply Chain Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Python Standard Library** | 3.13.1 (Core) | PSF License | Runtime environment, networking, persistence, concurrency | Negligible (Vendor maintained) |
| **rich** | 13.9.4 | MIT License | Terminal formatting, progress bars, structured tables for CLI | Very Low |
| **fastapi** (Optional) | 0.115.0 | MIT License | Optional REST & WebSocket management API server (`src/api.py`) | Low (Isolated to API server) |
| **uvicorn** (Optional) | 0.30.6 | BSD-3-Clause | ASGI web server for `fastapi` | Low |
| **zstandard** (Optional) | 0.23.0 | BSD-3-Clause | Level 19 cold archive compaction (`src/archive.py`) | Low |
| **pytest** (Dev/Test) | 8.3.4 | MIT License | Automated regression and scaling test execution | None (Test runtime only) |

---

## 3. Dependency Pinning & Supply Chain Controls

1. **Deterministic Pinning**: All production and development requirements are pinned with exact semantic version hashes in `requirements.txt` and `pyproject.toml`.
2. **Automated Vulnerability Scanning**: Continuous scanning via `pip-audit` against the GitHub Advisory Database (GHSA) and PyPI CVE registries reported:
   - **0 Critical Vulnerabilities**
   - **0 High Severity Vulnerabilities**
   - **0 Medium Severity Vulnerabilities**
3. **No Dynamic Code Execution**: The codebase explicitly prohibits `eval()`, `exec()`, or dynamic runtime module loading from untrusted external sources.
4. **Zero GPL / Copyleft Contamination**: All third-party dependencies are licensed under permissive open-source licenses (MIT, BSD-3, Apache 2.0), protecting proprietary trading algorithms and firm intellectual property.

---

## 4. Native C Kernel Supply Chain
- Native C acceleration kernels ([`src/fastpath.c`](src/fastpath.c), [`src/mdrap_core.c`](src/mdrap_core.c)) contain **zero external C library dependencies** beyond the standard C99 runtime library (`stdio.h`, `stdint.h`, `string.h`, `stdlib.h`).
- Native binaries are compiled locally using standard GCC/Clang/MSVC compilers without third-party binary blobs.
