# MDRAP Phase 10 — Release Version Consistency & Packaging Audit

## 1. Executive Summary
This document provides the formal **Version Consistency Audit** for MDRAP Phase 10 leading into Release Candidate `v3.1.0-rc1`. It verifies semantic versioning compliance, package metadata alignment, companion package compatibility, and environment isolation.

## 2. Version Identification Matrix

| Artifact / Component | Location / Identifier | Declared Version | Consistency Status |
|:---|:---|:---:|:---:|
| **Platform Source Version** | [`src/mdrap/_version.py`](src/mdrap/_version.py#L8) | `3.1.0` | **MATCH** |
| **Package Init Export** | [`src/mdrap/__init__.py`](src/mdrap/__init__.py#L14) | `3.1.0` | **MATCH** |
| **PyProject Dynamic Hook** | [`pyproject.toml`](pyproject.toml#L122) | `mdrap._version.__version__` (`3.1.0`) | **MATCH** |
| **Core Distribution Wheel** | `dist/mdrap_core-3.1.0-py3-none-any.whl` | `3.1.0` | **MATCH** |
| **Release Candidate Tag** | Git working tree baseline | `v3.1.0-rc1` (commit `25fc850`) | **MATCH** |
| **Legacy Global Environment** | User site-packages (`AppData/Roaming/...`) | `2.3.0` | **SHADOW DETECTED / ISOLATED** |

## 3. Shadow Import Detection & Resolution
During pre-flight packaging tests, an un-isolated `python -c "import mdrap"` resolved to a legacy `2.3.0` single-file module (`mdrap.py`) present in the user-level Python site-packages directory.
- **Root Cause**: Python's default site-packages search order picks up user-level installs if `PYTHONPATH` or isolated flags (`-S`) are omitted.
- **Verification Proof**: The isolated wheel validation harness (`scripts/verify_phase10_wheel.py`) executed with `python -S` against a dedicated `site-packages/` target confirmed:
  ```text
  MDRAP_VERSION: 3.1.0
  MODELS_SENTINEL: True
  INGESTLOG_SENTINEL: True
  ```
  Zero bleed-through from ambient environment occurred under isolated execution.

## 4. Companion Packages Dependency Compatibility
The modular core split established in Phase 8 isolates non-core components into companion packages under `packages/`:
- `mdrap-options`: version `1.0.0` (Requires `mdrap-core>=3.0.0`)
- `mdrap-analytics`: version `1.0.0` (Requires `mdrap-core>=3.0.0`)
- `mdrap-strategies`: version `1.0.0` (Requires `mdrap-core>=3.0.0`)
- `mdrap-contrib-vessel`: version `1.0.0` (Requires `mdrap-core>=3.0.0`)

All 4 companion packages specify `>=3.0.0`, satisfying forward compatibility with `mdrap-core==3.1.0`.

## 5. Dependency Pinning Verification
- **Core Platform Dependencies**: Exactly `0` external dependencies required for core market-data ingestion, normalization, quality evaluation, consensus, and durability.
- **Optional CLI Dependencies**: `rich>=13.0.0` for interactive terminal UI.
- **Optional API Dependencies**: `fastapi>=0.110.0`, `uvicorn>=0.28.0` for REST/WebSocket server.

## 6. Audit Verdict
All repository version declarations, wheel build artifacts, and dependency constraints are strictly consistent at version `3.1.0`.
