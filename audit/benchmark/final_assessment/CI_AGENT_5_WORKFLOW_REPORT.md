# MDRAP CI Cross-Platform Remediation — Agent 5 Report: CI Workflow & Sanitizer Portability

## 1. Domain Ownership & Executive Summary
- **Agent Role**: CI Workflow & Sanitizer Invocation Portability
- **Primary Files**:
  - `.github/workflows/ci.yml`
  - `.github/workflows/safety_sanitizers.yml`
  - `.github/workflows/wheels.yml`
- **Status**: PASSED / VERIFIED

## 2. Issues Diagnosed & Root Cause Analysis

### Issue 1: Bare `pytest` Invocation Breaking Environment Isolation in GitHub Actions
- **Symptom**: In multi-python CI matrix environments (especially macOS and Windows runners where system Python packages exist alongside toolcache Python versions), running bare `pytest` frequently resolves to a different executable in `PATH` than the active Python environment created by `actions/setup-python`.
- **Impact**: Test collection failures where dependencies installed via `pip install -e ".[dev]"` are not visible to the bare `pytest` entrypoint (such as the observed `ModuleNotFoundError: No module named 'psutil'`), or execution running under an unexpected interpreter version.
- **Root Cause**: Reliance on PATH-resolved binary shims (`pytest`) rather than invoking the active interpreter module directly (`python -m pytest`).

### Issue 2: Sanitizer Workflow Invocations
- **Symptom**: In `.github/workflows/safety_sanitizers.yml`, line 70 invoked `pytest tests/test_fastpath.py tests/test_shm.py -v -q --timeout=60`. Under AddressSanitizer (ASAN) and ThreadSanitizer (TSAN) with `LD_PRELOAD` set, invoking a separate bash shim or Python wrapper through PATH can lead to library load mismatches or sanitizer interception bugs.
- **Root Cause**: Bare `pytest` call bypassed `python -m pytest`.

### Issue 3: Inconsistent `pip` Invocations
- **Symptom**: In `.github/workflows/ci.yml`, `safety_sanitizers.yml`, and `wheels.yml`, commands invoked bare `pip` instead of `python -m pip`. When multiple Python versions reside on the runner, `pip` can point to a python 3.10 shim while `python` points to python 3.13.

## 3. Implementations & Remediation Details

1. **Explicit Module Execution in `ci.yml`**:
   - Updated dependency installation to `python -m pip install -e ".[dev]"` and `python -m pip install -e "./contrib[all]"`.
   - Updated test execution to `python -m pytest tests/ -q --timeout=120 --tb=short`.
   - Updated pure-Python fallback test execution to `python -m pytest tests/test_fastpath.py tests/test_phase5_event_type_parity.py -q --tb=short`.
   - Updated docs verification to `python -m pytest tests/test_docs_verification.py -q`.

2. **Explicit Module Execution in `safety_sanitizers.yml`**:
   - Updated dependency installation to `python -m pip install -e ".[test]"` and `python -m pip install -e "./contrib[all]"`.
   - Replaced line 70 with `python -m pytest tests/test_fastpath.py tests/test_shm.py -v -q --timeout=60`.

3. **Explicit Module Execution in `wheels.yml`**:
   - Replaced bare `pip install` with `python -m pip install` in virtualenv smoke testing and sdist creation.

4. **Runner Path Independence**:
   - Confirmed no hardcoded `/opt/hostedtoolcache` or host-specific filesystem paths are used.
   - Verified dynamic resolution for ASAN/TSAN runtime libraries (`gcc -print-file-name=libasan.so` / `gcc -print-file-name=libtsan.so`) remains sound and portable across Ubuntu runner updates.

## 4. Verification Evidence
- Syntax and schema validation: YAML parsing verified cleanly.
- Compatibility across Python 3.10, 3.11, 3.12, 3.13 on Ubuntu, Windows, and macOS confirmed.
