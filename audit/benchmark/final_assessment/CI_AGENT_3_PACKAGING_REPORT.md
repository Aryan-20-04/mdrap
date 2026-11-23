# CI AGENT 3: Cross-Platform Pure Wheel Build and Packaging Report

## Executive Summary
Agent 3 diagnosed and remediated the cross-platform wheel build failure (`CalledProcessError`) affecting Ubuntu Linux (Python 3.12), Windows (Python 3.11), and macOS (Python 3.13), ensured PEP 517 build frontend/backend dependency completeness (`wheel>=0.40.0` in `[build-system]`, `test`, and `dev`), and improved test failure diagnostics.

## Original Failure
- **Platforms**: Ubuntu Linux (Python 3.12), Windows (Python 3.11), macOS (Python 3.13)
- **Test**: `tests/test_wheel_binary_exclusion.py::test_pure_wheel_contains_no_compiled_binaries`
- **Subprocess**: `python -m build --wheel --no-isolation` failed with `CalledProcessError`.

## Root Cause Analysis
1. **Missing `wheel` Package in Build & Test Environments**:
   When invoking `python -m build --wheel --no-isolation`, `build` delegates wheel packaging to `setuptools.build_meta`. Under `--no-isolation`, setuptools requires the `wheel` package installed in the active environment.
   In CI runners using `actions/setup-python@v5`, `wheel` is not pre-installed by default. In `pyproject.toml`, `wheel` was omitted from `[build-system].requires = ["setuptools>=61.0"]` and omitted from `dev` and `test` dependency groups.
2. **Hidden Subprocess Diagnostics in Test**:
   `tests/test_wheel_binary_exclusion.py` used `subprocess.run(..., check=True, capture_output=True)`. When `check=True` triggered `subprocess.CalledProcessError`, Python concealed stdout and stderr in standard tracebacks, obscuring the underlying build error.

## Design & Implementation
1. **Explicit Build System & Packaging Dependencies (`pyproject.toml`)**:
   - Updated `[build-system]` to explicitly declare `wheel`:
     ```toml
     [build-system]
     requires = ["setuptools>=61.0", "wheel>=0.40.0"]
     build-backend = "setuptools.build_meta"
     ```
   - Added `"build>=1.0.0"` and `"wheel>=0.40.0"` to `project.optional-dependencies.test` and `project.optional-dependencies.dev`.
2. **Subprocess Diagnostic Exposure in Test Harness**:
   - Refactored `tests/test_wheel_binary_exclusion.py` to capture and format complete stdout, stderr, working directory, and python executable upon build failure.
3. **Pure-Python Wheel Binary Exclusion Verification**:
   - `CleanBuildPy` in `src/mdrap_build.py` and `MANIFEST.in` global excludes were re-verified: zero `.so`, `.dll`, `.dylib`, or `.pyd` files are included in `mdrap_core-6.0.8-py3-none-any.whl`.

## Verification Evidence
1. `pytest tests/test_wheel_binary_exclusion.py -v` -> **1 passed, 0 failed**.
2. `python scripts/verify_release_artifacts.py` -> **Passed cleanly**:
   - `mdrap_core-6.0.8-py3-none-any.whl`: 106 entries, 0 leaked binaries.
   - `mdrap_compat-6.0.8-py3-none-any.whl`: 94 entries, 0 leaked binaries.
   - `mdrap_contrib-6.0.8-py3-none-any.whl`: 5 entries, 0 leaked binaries.
   - All source distributions verified.
