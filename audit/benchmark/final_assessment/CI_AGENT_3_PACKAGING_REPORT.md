# MDRAP CI Cross-Platform Remediation — Agent 3 Report: Cross-Platform Build Backend and Pure Wheel

## 1. Domain Ownership & Executive Summary
- **Agent Role**: Cross-Platform Build Backend & Pure Wheel Integrity
- **Primary Files**:
  - `pyproject.toml`
  - `compat/pyproject.toml`
  - `contrib/pyproject.toml`
  - `tests/test_wheel_binary_exclusion.py`
  - `scripts/verify_release_artifacts.py`
- **Status**: PASSED / VERIFIED

## 2. Issues Diagnosed & Root Cause Analysis

### Issue 1: macOS Python 3.11 Setuptools Schema Rejection (`project.license`)
- **Symptom**: On macOS with Python 3.11, building wheels or sdist failed with:
  `project.license must be valid exactly by one definition`
- **Root Cause**: In PEP 621, `project.license` was originally defined as a table containing either `file` or `text` (e.g. `license = { text = "MIT" }`). The bare SPDX string format (`license = "MIT"`) was specified in PEP 639 and is only recognized by setuptools >= 77.0.0. The setuptools version on macOS Python 3.11 runner strictly enforced the PEP 621 table schema and rejected the bare string `"MIT"`.
- **Remediation**: Updated `license = { text = "MIT" }` across `pyproject.toml`, `compat/pyproject.toml`, and `contrib/pyproject.toml`. This table format is universally supported across all setuptools versions (>= 61.0 through 81.0+).

### Issue 2: Windows and Ubuntu Python 3.13 Missing Setuptools in Non-Isolated Wheel Builds
- **Symptom**: On Windows and Ubuntu runners with Python 3.13, `test_wheel_binary_exclusion.py` failed with:
  `ModuleNotFoundError: No module named 'setuptools'`
- **Root Cause**:
  `test_wheel_binary_exclusion.py` executes:
  ```bash
  python -m build --wheel --no-isolation
  ```
  When `--no-isolation` is passed, `build` uses the active Python environment directly without creating a temporary venv. In Python 3.12+, Python eliminated `setuptools` from the default environment. While `setuptools` was declared in `[build-system].requires`, pip only provisions it into an isolated build container during `pip install -e`. Because `setuptools` was omitted from `[project.optional-dependencies].test` and `dev`, the test runner environment lacked `setuptools`.
- **Remediation**:
  Declared `"setuptools>=61.0"` in `project.optional-dependencies.test` and `dev` in `pyproject.toml`. When `pip install -e ".[dev]"` executes during CI setup, `setuptools` is explicitly installed in the active interpreter.

## 3. Pure-Wheel Binary Cleanliness Evidence
- Verified `test_pure_wheel_contains_no_compiled_binaries` passed cleanly.
- Inspected wheel contents:
  - `mdrap_core-6.0.8-py3-none-any.whl`: 106 entries, exactly 0 `.dll`, `.so`, `.dylib`, or `.pyd` files.
  - `mdrap_compat-6.0.8-py3-none-any.whl`: 94 entries, exactly 0 compiled binaries.
  - `mdrap_contrib-6.0.8-py3-none-any.whl`: 5 entries, pure package.
- `scripts/verify_release_artifacts.py`: All 6 release artifacts (`core`, `compat`, `contrib` sdists and wheels) passed all inspection and isolation gates.
