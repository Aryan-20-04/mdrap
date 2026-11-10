# MDRAP v5.3.0 Remediation Changelog

This document logs all engineering rectifications performed during the MDRAP v5.3.0 Multi-Agent Remediation, Integration & Release Recovery operation on branch `remediation/v5.3.0-release-recovery`.

---

## 1. Package Architecture & Release Distribution (Agent 1 & Agent 7)

### Problem
- In release archive `mdrap_v5.3.0_release.zip`, all companion domain packages under `packages/` (`mdrap-strategies`, `mdrap-audit`, `mdrap-analytics`, `mdrap-enterprise`, `mdrap-options`, `mdrap-contrib-vessel`) were missing.
- Root cause: `.gitignore` had ignored `packages/` globally in commit `98706dc`, which caused `git archive` and zip packaging workflows to omit `packages/`.
- Tests attempting to import `mdrap_strategies.backends`, `matching_engine`, etc., failed with collection errors on bare environments.

### Remediation
- **`.gitignore` update**: Removed `packages/` from `.gitignore` so that companion packages are fully tracked in Git and included in source release archives. Added `dist/` and build caches to gitignore.
- **Standalone Wheel Builds**: Created script [`scripts/build_commercial_packages.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/scripts/build_commercial_packages.py) which compiles all 6 companion packages into independent wheels:
  - `dist/packages/mdrap_strategies-1.0.0-py3-none-any.whl` (43,091 bytes, SHA-256 `15c722e2...`)
  - `dist/packages/mdrap_audit-1.0.0-py3-none-any.whl` (5,466 bytes, SHA-256 `2cd7e30d...`)
  - `dist/packages/mdrap_analytics-1.0.0-py3-none-any.whl` (6,313 bytes, SHA-256 `8309268d...`)
  - `dist/packages/mdrap_enterprise-1.0.0-py3-none-any.whl` (4,347 bytes, SHA-256 `d01298b1...`)
  - `dist/packages/mdrap_options-1.0.0-py3-none-any.whl` (7,599 bytes, SHA-256 `6d0ed7e8...`)
  - `dist/packages/mdrap_contrib_vessel-1.0.0-py3-none-any.whl` (10,071 bytes, SHA-256 `02b553f8...`)
- **Core Distribution Build**:
  - `dist/mdrap_core-5.3.0-py3-none-any.whl` (878,686 bytes, SHA-256 `e9462d1d...`)
  - `dist/mdrap_core-5.3.0.tar.gz` (742,872 bytes, SHA-256 `da080da4...`)
- **Verification**: Verified standalone editable installation via `pip install -e packages/mdrap-strategies` and direct importability without sys.path hacks.

---

## 2. Cryptographic Integrity & RFC 6962 Compliance (Agent 2)

### Problem
- In [`packages/mdrap-audit/mdrap_audit/__init__.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/packages/mdrap-audit/mdrap_audit/__init__.py), internal node hashing concatenated ASCII hex strings (`b"\x01" + (left + right).encode("utf-8")`) creating 129-byte inputs rather than the RFC 6962 Section 2.1 standard (65-byte input: 1 byte prefix `0x01` + 32-byte left hash + 32-byte right hash).
- Audit tree lacked RFC 6962 consistency proofs for historical append-only verification across tree sizes.

### Remediation
- Re-architected `AuditMerkleTree` in `packages/mdrap-audit/mdrap_audit/__init__.py`:
  - Enforced strict binary domain separation: `b"\x00"` prefix for leaves, `b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)` for internal nodes.
  - Implemented Section 2.1.2 consistency proofs via `get_consistency_proof(m, n)` and `ConsistencyProof.verify()`.
  - Added test suite [`tests/test_rfc6962_audit.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_rfc6962_audit.py) validating 100% bit-exact compliance against Certificate Transparency canonical test vectors (tree sizes 0 through 8).

---

## 3. Supply Chain Security & CVE Elimination (Agent 5)

### Problem
- `pip-audit -r requirements.txt` identified high-severity supply chain vulnerability `PYSEC-2026-1845` affecting `pytest 8.4.2`.

### Remediation
- Upgraded `pytest` to `9.2.0` and `pytest-asyncio` to `1.4.1.post1`.
- Updated version constraints in `requirements.txt` (`pytest>=9.0.3`, `pytest-asyncio>=1.0.0`) and `pyproject.toml`.
- Re-ran `pip-audit`: Scanned all 35 direct and transitive production dependencies. **0 vulnerabilities detected**.
- Saved machine-readable audit evidence in [`audit/v5.3.0-remediation/DEPENDENCY_AUDIT.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.3.0-remediation/DEPENDENCY_AUDIT.json).

---

## 4. Native Acceleration & Python Differential Parity (Agent 4 & Agent 6)

### Problem
- Absence of an explicit automated differential test suite comparing pure Python `QualityEngine` and native C `FastQualityEngine` across corner cases, crossed books, and microsecond timestamps.

### Remediation
- Implemented [`tests/test_fastpath_differential.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_fastpath_differential.py):
  - Ingests valid events, crossed-book anomalies, zero/negative prices, and large timestamps through both engines.
  - Verifies that `FastQualityEngine` and pure Python `QualityEngine` produce bit-exact canonical status, violation codes, and reason sequences.
- Added Gate 9 to [`scripts/check.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/scripts/check.py) running both the native FFI micro-benchmark smoke check and differential parity suite.

---

## 5. Local Validation Protocol Reorganization (Agent 3)

### Problem
- In [`scripts/check.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/scripts/check.py), native compilation (Gate 7) was scheduled *after* test execution (Gate 4), causing unit tests to run against stale or uncompiled native artifacts.

### Remediation
- Reorganized validation protocol into strict 9-gate pipeline:
  1. Gate 1: Code Formatting (`ruff format --check src/`)
  2. Gate 2: Static Analysis (`ruff check src/`)
  3. Gate 3: Single-Source Rules Parity & Claims Governance (`gen_reasons.py`, `claims_scanner.py`)
  4. Gate 4: Native C Accelerator Compilation (`build_fastpath.py` - compiles `.dll`, `.exe`, `.pyd`)
  5. Gate 5: Core Unit Tests (`pytest -m "not slow and not network"`)
  6. Gate 6: Integration, Durability & Packaging Tests
  7. Gate 7: Security Hardening & Cryptographic Audit Tests
  8. Gate 8: Supply Chain Dependency CVE Audit (`pip-audit`)
  9. Gate 9: Native Accelerator Smoke Check & Differential Parity
- Added automated XML junit reporting and consolidation into authoritative `TEST_RESULTS.json`.

---

## 6. Claims Scanner & AST Governance (Agent 3 & Agent 4)

### Problem
- Claims scanner lacked provenance checking and crashed if invoked without explicit metadata arguments.
- Claims manifest was not dynamically updated from authoritative test outputs.

### Remediation
- Added `get_authoritative_metadata()` in `scripts/claims_scanner.py` linking directly to `src/mdrap/_version.py`.
- Made `auth_meta` optional with fallback in `verify_claims_manifest_provenance()`.
- Synchronized `claims_manifest.json` with authoritative test counts from `TEST_RESULTS.json`.

---

## 7. Test Isolation & Subsystem Fallbacks

### Problem
- In `tests/test_companion_absence.py`, monkeypatching `MDRAP_DISABLE_COMPANIONS="1"` left stub modules in `sys.modules`, which polluted subsequent test execution (`tests/test_vessel_fastpath.py`) with missing attributes.
- In `src/mdrap/vessel.py`, stub fallback definitions lacked `_HAS_FASTPATH = False` and `_C_CHOKEPOINTS = None`.

### Remediation
- Updated `test_companion_absence.py` with a `finally` block purging mocked modules from `sys.modules`.
- Added explicit fallback attributes `_HAS_FASTPATH = False` and `_C_CHOKEPOINTS = None` in `src/mdrap/vessel.py`.
- All tests now execute in arbitrary sequence with zero cross-test state leakage.
