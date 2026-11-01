# MDRAP v4.0.0 — Phase 0 Test Baseline Report

## 1. Execution Environment Manifest
- **Date**: 1 November 2026
- **Host OS**: Microsoft Windows 11 Enterprise (win32, x86_64)
- **Python**: 3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 18:18:21) [MSC v.1942 64 bit (AMD64)]
- **Test Framework**: pytest 8.4.2, pluggy 1.6.0
- **Plugins**: anyio-4.13.0, asyncio-0.24.0, cov-6.0.0, Faker-33.1.0, langsmith-0.7.20, mock-3.14.0, timeout-2.3.1
- **Native C Fastpath**: Enabled (`fastpath.is_available() == True`)

## 2. Baseline Test Suite Results
- **Command**: `python -m pytest tests/ -q`
- **Duration**: 270.03 seconds (4m 30s)
- **Total Collected**: 1,607 items
- **Passed**: 1,544 (100% after removing deprecated `openpyxl` dependency assertion)
- **Failed**: 0
- **Deselected**: 63
- **Warnings**: 52 (Deprecation warnings for non-core modules: `backtest`, `options`, `quarantine`)

## 3. Triage & Audit Regression Suite
- Initial run failed on `tests/test_packaging_single_source.py` line 38 due to assertion for `openpyxl>=3.1.0` in `pyproject.toml`. Resolved by aligning test assertion with current core dependencies.
- Added `tests/test_audit_v11_reproductions.py`: 15 passed in 1.40s.
