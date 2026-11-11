# MDRAP Release Final Gate — Baseline & Anomaly Resolution Audit

**Audit Date**: 2026-11-11  
**Lead Architect & Auditor**: Lead Engineering Architect / Release Gatekeeper  
**Target Milestone**: MDRAP Institutional Reliability Platform Release  
**Working Branch**: `cleanup/product-scope-consolidation`  
**Active Commit**: `4cf15c7` (tracking remote `origin/cleanup/product-scope-consolidation`)  
**Base Release Tag**: `v5.3.0`  

---

## 1. Executive Summary & Verification Context

Following the strategic product reset and codebase consolidation, this baseline audit independently inspects the git repository state, package metadata, test execution realities, native acceleration delivery mechanisms, and public API boundaries prior to authorizing any merge or release promotion.

All claims in this document derive directly from executable evidence, SHA-256 artifact verification, and clean-environment execution.

---

## 2. Environment & Repository Baseline

| Parameter | Observed Value | Ground Truth Verification |
| :--- | :--- | :--- |
| **Operating System** | Windows 11 Enterprise (AMD64) | `sys.platform == 'win32'` |
| **Python Version** | Python 3.13.1 (64-bit) | `C:\Python313\python.exe` |
| **Git Branch** | `cleanup/product-scope-consolidation` | Up to date with remote `origin` |
| **Source Commit** | `4cf15c7` | Commit log validated |
| **Working Tree** | Clean | `git status` clean |
| **Original Tag** | `v5.3.0` (`f5f6142` / `82af13c`) | Tag preserved; history untouched |
| **Wheel Artifact** | `dist/mdrap_core-5.3.0-py3-none-any.whl` | 766,039 bytes |
| **Sdist Artifact** | `dist/mdrap_core-5.3.0.tar.gz` | 715,487 bytes |

### Artifact Hashes (SHA-256)
- **Wheel (`dist/mdrap_core-5.3.0-py3-none-any.whl`)**:
  `A6D4D0DAABF472633BD33F79103034563F6FBA4DA611A2C0E7699A9CC4BE14C5`
- **Sdist (`dist/mdrap_core-5.3.0.tar.gz`)**:
  `8A1D89FF468483A9AE550B60E814900B7DB08F27842FC07C81B490F5188E48DA`

---

## 3. Resolution of Known Inconsistencies

### Inconsistency A: Test Execution vs. Collection Metrics
**Investigation & Findings**:
1. **The Confusion**: Previous consolidation summaries noted 1,719 test items collected, 40 deselected, and 1,679 selected, but highlighted 36 passing tests from quick smoke suites (`test_product_assurance.py`, `test_cli.py`, `test_pilot_readiness.py`).
2. **Pytest Marker Mechanism**: Inspection of `pytest.ini` revealed line 5:
   ```ini
   addopts = -m "not slow and not network"
   ```
   Exactly 40 test items are tagged `@pytest.mark.slow` (long-running soak tests, high-throughput saturation benchmarks) or `@pytest.mark.network` (requiring external exchange network connections). Pytest automatically deselects these 40 tests when running default invocations.
3. **Execution Reality**:
   Running the full test suite against all 1,678 selected test items initially surfaced 41 errors and 5 failures due to un-namespaced mock patches (`tests/test_chd.py`, `tests/test_phase11_storage_durability.py`), a lingering test for pruned trading subcommands (`tests/test_coverage_booster.py`), an un-prefixed import in `scripts/backup.py`, an outdated claim string in `audit/claims_manifest.json`, and an unchecked length boundary in `IngestLog.verify()`.
4. **Resolution & Current Count**:
   All 5 root causes were resolved in commit `4cf15c7`.
   A complete, timed execution of the entire test suite was executed:
   - **Collected**: 1,718 items
   - **Deselected**: 40 items (`not slow and not network`)
   - **Selected**: 1,678 items
   - **Executed**: 1,678 items
   - **Passed**: 1,677 tests
   - **Skipped**: 1 test (`tests.test_phase7_benchmark_integrity::test_f02_f03_failover_results_consistent_with_raw_samples` — expectedly skipped because raw soak sample artifacts are omitted from checkout)
   - **Failed**: 0
   - **Errors**: 0
   - **Duration**: 386.38 seconds (6 minutes 26 seconds)
   - **Machine-Readable Log**: Stored at [`audit/release-final/pytest_results.xml`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/release-final/pytest_results.xml).

---

### Inconsistency B: Native Package Distribution & Pure Python Fallback
**Investigation & Findings**:
1. **The Wheel Architecture**:
   The built wheel is tagged `mdrap_core-5.3.0-py3-none-any.whl`.
   Inspection of the zip file contents via `zipfile.ZipFile` revealed that `mdrap/_fastpath_native.dll` was included due to `package-data` configuration in `pyproject.toml`.
2. **Platform Compatibility & Fallback**:
   - On Windows x86_64, `ctypes.CDLL` loads `_fastpath_native.dll` if present, setting `HAS_FASTPATH = True`.
   - On Linux or macOS, the Windows PE `.dll` is skipped by candidate discovery (`candidates = ["_fastpath_native.so", "fastpath.so", ...]`).
   - If `$env:MDRAP_DISABLE_FASTPATH="1"` is set on any platform, or if the native shared library is absent, `src/mdrap/fastpath.py` transparently falls back to `QualityEngine` (pure Python).
3. **Clean-Environment Empirical Test**:
   The package was reinstalled via `pip install --force-reinstall` and tested in `$env:TEMP` (outside the repository):
   - **Default Native Mode**: `HAS_FASTPATH == True`, `Engine: OK`.
   - **Pure Python Fallback Mode** (`MDRAP_DISABLE_FASTPATH=1`): `HAS_FASTPATH == False`, `Engine: OK`.
   - Both modes produce 100% identical validation status and reason bitmasks on all 15 quality rules.
4. **Packaging Governance Ruling**:
   A wheel tagged `py3-none-any` should be documented as providing the portable, pure-Python baseline across all operating systems. Users on Windows receive pre-compiled acceleration automatically, while Linux/macOS users compile via `python -m mdrap.build_fastpath` or run the validated pure-Python engine with zero degradation in accuracy.

---

### Inconsistency C: Dependency Baseline & Optional Extras
**Investigation & Findings**:
1. **Core Runtime Dependencies**:
   `pyproject.toml` line 39: `dependencies = []`.
   `src/mdrap_core.egg-info/requires.txt` contains zero un-sectioned dependencies.
   MDRAP core engine runs 100% on standard library Python (`sqlite3`, `hashlib`, `zlib`, `struct`, `ctypes`, `threading`, `json`, `argparse`).
2. **Optional Component Extras**:
   - `[api]`: `fastapi>=0.110.0`, `uvicorn>=0.28.0`, `httpx>=0.27.0` (read-only telemetry REST/WebSocket API).
   - `[ui]`: `rich>=13.0.0` (interactive ANSI console tables and live dashboard).
   - `[feeds]`: `websockets>=12.0,<17.0` (live public crypto exchange connectors).
   - `[export]`: `pyarrow>=20.0.0` (Parquet forensic export).
   - `[columnar]`: `duckdb>=0.9.0`, `pyarrow>=20.0.0` (OLAP tick query engine).
   - `[config]` / `[yaml]`: `pyyaml>=6.0` (YAML configuration parser; stdlib TOML/JSON parser used when absent).
   - `[kafka]`: `confluent-kafka>=2.3.0` (optional decoupled stream distribution sink).
3. **Build Dependencies**:
   - `setuptools>=61.0`, `wheel`, `build>=1.0.0`.

---

### Inconsistency D: Public Compatibility & Removal of Root Forwarding Shims
**Investigation & Findings**:
1. **Root Shims Were Never In Wheels**:
   In all prior releases (v4.0.1, v5.3.0), `pyproject.toml` strictly declared `include = ["mdrap", "mdrap.*"]` and `py-modules = []`.
   External consumers who installed `mdrap-core` via PyPI or wheels never received `src/bbo.py`, `src/gateway.py`, etc. They ALWAYS imported `from mdrap import Engine`, `from mdrap.bbo import ...`.
   Eliminating the 92 root files removed internal checkout shadowing without affecting wheel consumers.
2. **Pruned Out-of-Scope Modules**:
   - Removed: `src/mdrap/navigator.py` (curses/rich interactive navigation terminal), `src/mdrap/trading_cli.py` (retail news scrapers, corporate actions, retail watchlists), `src/mdrap/af_xdp.py` (unimplemented Linux kernel bypass stubs), `src/mdrap/fx.py` (retail forex conversion).
   - Retained as backward-compatibility shims: `src/mdrap/vessel.py` and `src/mdrap/options.py` (clean shims raising `CompanionPackageMissing` for `packages/mdrap-contrib-vessel` and `packages/mdrap-options`).
   - Retained in core: `src/mdrap/terminal_display.py` (compact, headless-safe ANSI console visualizers for telemetry).
3. **Semantic Versioning Assessment**:
   Because `mdrap.navigator` and `mdrap.trading_cli` were removed from the `mdrap` package namespace, strict SemVer defines this as a breaking API change for any consumer who may have imported those modules. Consequently, the release should be promoted as a new minor or major release (e.g. `v5.4.0` or `v6.0.0`).
