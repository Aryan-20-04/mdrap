# Call-Site Security Review & Disposition Record (X-01)

**Standard**: Phase B, Item B4 & Universal Test Specification Section 6.9 (`X-01`).  
**Date**: 2026-10-18  
**Scope**: All Python source files located under `src/` (`src/mdrap/`).  
**Reviewer**: Antigravity Core Systems Audit  

---

## 1. Executive Summary

An exhaustive static scan was executed across all production Python code in `src/` targeting dangerous patterns and functions: `eval()`, `exec()`, `pickle`, `shell=True`, `os.system()`, and non-test `assert` statements.

| Pattern | Detected Count | Disposition | Risk Assessment |
| --- | :---: | --- | --- |
| `eval(` | **0** | Clean | None |
| `exec(` | **0** | Clean | None |
| `pickle` | **0** | Clean | None |
| `shell=True` | **0** | Clean | None |
| `os.system(` | **3** | Approved (TUI Screen Clear Only) | Low (Non-parameterized fixed string) |
| Non-test `assert` | **0** | Clean (Replaced with explicit `RuntimeError`) | None (Resilient under `python -O`) |

---

## 2. Detailed Findings & Dispositions

### 2.1 Dynamic Evaluation (`eval`, `exec`)
- **Findings**: 0 occurrences.
- **Disposition**: PASS. Dynamic code execution is strictly prohibited by MDRAP coding standards.

### 2.2 Serialization Hazards (`pickle`)
- **Findings**: 0 occurrences.
- **Disposition**: PASS. Market data persistence uses typed binary framing (`IngestLog`), IEEE-754 fixed/floating primitives, JSON for raw payloads, and SQLite/DuckDB columnar projections. No untrusted object deserialization is permitted.

### 2.3 Shell Execution (`subprocess.Popen(..., shell=True)`)
- **Findings**: 0 occurrences.
- **Disposition**: PASS. All subprocess invocations pass explicit argument vectors (`list[str]`) without shell interpolation.

### 2.4 System Calls (`os.system`)
- **Findings**: 3 occurrences located in interactive console utilities:
  1. `src/mdrap/service.py:1387`: `os.system("cls")` — Terminal UI loop screen clearing.
  2. `src/mdrap/service.py:1406`: `os.system("cls")` — Terminal UI loop screen clearing.
  3. `src/mdrap/cli/__init__.py:2551`: `os.system("cls" if sys.platform == "win32" else "clear")` — Headless monitor CLI dashboard clearing.
- **Disposition**: APPROVED. All invocations are fixed literals ("cls" / "clear") with zero external string interpolation or user-provided arguments.

### 2.5 Invariant Assertions (`assert` under `python -O`)
- **Remediation**:
  - `src/mdrap/engine.py:482`: Assertion validating WAL-allocated monotonic offsets matching `raw_to_offset` was replaced with an explicit `RuntimeError("WAL allocated offsets diverged: ...")`.
  - `src/mdrap/journal.py:48`: Struct size validation was replaced with an explicit `RuntimeError("JOURNAL_HDR_STRUCT size mismatch: ...")`.
- **Status**: 0 non-test `assert` statements remain in `src/`. All invariant checks persist under Python optimized execution (`python -O`).

---

## 3. Compliance Verification Test

Verified via automated test `test_x01_call_site_review_record` in `tests/test_claims_verification.py`.
