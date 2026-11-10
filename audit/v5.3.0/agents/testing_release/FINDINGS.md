# Testing, Build & Release Engineering — Detailed Findings

## Executive Summary
Audited test suite architecture (304 files, ~1,735 tests), golden oracle parity tests, CI/CD workflows, build tools, and quality gates. Golden parity and binary serialization tests are rigorous. However, CRITICAL defects were discovered: assertion swallowing in hot-path tests, a rubber-stamp claims gate in `gen_claims.py`, non-portable `-march=native` compiler flags in release wheels, and version drift (4.0.1 vs 5.3.0).

## Categorized Findings

### CRITICAL
1. **F-TST-01: Critical Assertion Swallowing in Hot-Path Parity Test**:
   - `tests/test_fastpath.py:81-86`: In the per-tick Python vs C parity loop:
     ```python
     try:
         assert res_py.quality_status == res_c.quality_status
     except Exception:
         pass
     ```
   - Catches `Exception`, which catches `AssertionError` and silently suppresses parity failures!

2. **F-TST-02: Rubber-Stamp Documentation Claims Gate in `scripts/gen_claims.py`**:
   - `scripts/gen_claims.py:155-157`: When run with `--check`, extracts `current_claim` from markdown but **never asserts equality** against `claim_str`. Unconditionally prints `[PASSED]`.

3. **F-TST-03: Non-Portable Native C Compiler Flags in Wheels**:
   - `src/mdrap/build_fastpath.py:152, 301`: Compiles with `-march=native`.
   - Binary wheels built on GitHub Actions runners bake runner-CPU-specific instructions, triggering `SIGILL` on older client CPUs.

4. **F-TST-04: Version Desynchronization Across Release Manifests**:
   - `src/mdrap/_version.py` is `4.0.1`; `claims_manifest.json` is `4.0.1`; documentation declares `v5.3.0`.

### HIGH
5. **F-TST-05: Missing SDist in PyPI Release Workflow**:
   - `.github/workflows/wheels.yml:55-69`: Builds and publishes binary wheels, but omits source distribution (`.tar.gz`).

6. **F-TST-06: Shared Memory Test Resource Leaks**:
   - `tests/test_shm.py:21-155`: Allocates hardcoded segment names without `try...finally` cleanup. Unhandled assertion leaves segments in `/dev/shm`.

7. **F-TST-07: Incomplete Pure Python Fallback CI Verification**:
   - `.github/workflows/ci.yml:49-53`: Only runs a single test file under `MDRAP_DISABLE_FASTPATH="1"`. 303 test files are never evaluated in pure Python mode in CI.

8. **F-TST-08: Missing Dedicated Unit Tests for Core Production Modules**:
   - Over 20 key modules lack dedicated test files: `network.py`, `supervisor.py`, `profile.py`, `gap.py`, `adapters/`.

### MEDIUM
9. **F-TST-09: Vacuous Assertion in Daemon BBO Query Test**:
   - `tests/test_service.py:68-74`: `if bbo is not None: assert ...`. If daemon is slow to start, `bbo` is `None` and zero assertions run.
10. **F-TST-10: Quality Protocol Gate 4 Excludes Integration Tests**:
    - `scripts/check.py:148-168`: `-k "not integration"` filters out all integration tests, but Gate 5 only runs 3 specific files.\n