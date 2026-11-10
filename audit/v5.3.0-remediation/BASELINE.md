# MDRAP v5.3.0 Remediation & Release Recovery — Repository Baseline

**Document Version**: 5.3.0-RECOVERY-BASELINE  
**Classification**: Engineering Recovery Baseline  
**Lead Orchestrator**: Multi-Agent Lead Engineering Orchestrator  
**Date**: November 10, 2026  
**Dedicated Branch**: `remediation/v5.3.0-release-recovery`  
**Base Commit**: `a43c4d81cbbcfc10f7fc4be37d2a3ec4346c0d29` (`chore(release): merge v5.3.0 multi-agent audit remediations and release certification`)  
**Parent Tag**: `v5.3.0` (unmodified)  

---

## 1. System & Environment Baseline

- **Operating System**: Windows 11 Enterprise AMD64 (Version 10.0.26200-SP0) / POSIX Linux Parity Target
- **Python Runtime**: Python 3.13.1 (MSC v.1942 64-bit AMD64)
- **C Compilers Available**: GCC (MinGW-w64 14.2.0), MSVC 14.44 (`cl.exe`), Clang/LLVM 18.1
- **SIMD / Hardware**: AVX2, SSE4.2, FMA enabled
- **Git Branch**: `remediation/v5.3.0-release-recovery`
- **Git Status**: Clean working tree on branch creation

---

## 2. Package Structure & Missing Distribution Packages

### 2.1 Repository Layout
- **Core Engine**: Located in `src/mdrap/` (Version `5.3.0` declared in `src/mdrap/_version.py`).
- **Distribution Configuration**: Root `pyproject.toml` configures `name = "mdrap-core"` with `package-dir = {"" = "src"}`.
- **Companion Packages on Disk**:
  - `packages/mdrap-strategies` (Modules: `mdrap_strategies`, `backends`, `matching_engine`, `microstructure`, `risk_engine`, `fx_basis`, `tear_sheet`)
  - `packages/mdrap-audit` (Modules: `mdrap_audit`, `InclusionProof`, `MerkleAuditTree`, `TimestampAnchor`)
  - `packages/mdrap-analytics`
  - `packages/mdrap-enterprise`
  - `packages/mdrap-options`
  - `packages/mdrap-contrib-vessel`

### 2.2 Root Cause of Companion Package Exclusion
- **Defect Finding**: Commit `98706dcecfca09631776667f5424435cebcc16c2` added `packages/` to `.gitignore` under `# Commercial / Companion Packages (Kept Local / Private Only)`.
- **Impact on Release ZIP**: Because Git ignores `packages/`, `git archive` generated `mdrap_v5.3.0_release.zip` containing 1,128 files but **zero (0) package files from `packages/`**.
- **Impact on Installation**: Neither `mdrap-strategies` nor `mdrap-audit` were installed when running `pip install .` or installing wheels from repository root.
- **Impact on External Imports**: Running `python -c "import mdrap_strategies"` outside the repository or without custom `sys.path` injection fails immediately with `ModuleNotFoundError`.

---

## 3. Test Collection & Existing Validation Gate Behavior

### 3.1 Test Collection Summary
- **Collection Command**: `pytest tests/ --collect-only`
- **Collected**: 1,802 test items across 72 test suites.
- **Collection Failure Points**:
  - Tests running in isolated virtual environments or external runners without companion packages installed fail on `import mdrap_strategies` or `import mdrap_audit`.
  - Swallowed assertions were removed in remediation `baf70b4`, but companion package discovery remains disconnected from release packaging.

### 3.2 Existing 9-Gate Protocol (`scripts/check.py`) Deficiencies
- **Gate Ordering Inversion**:
  - Gate 4 (Core Unit Tests) imports native extensions (`fastpath.py`, `_fastpath_native.dll`, `_fastpath_c.pyd`).
  - Gate 8 compiles them via `python build_fastpath.py`.
  - **Result**: On a clean checkout without pre-compiled binaries, Gate 4 fails immediately. Compilation must precede test execution.
- **Gate 7 Incomplete Dependency Audit**:
  - Gate 7 only verifies version strings for `pyarrow` and `pytest` in `requirements.txt`.
  - No automated vulnerability scanning (e.g. `pip-audit`) is performed against the transitive dependency tree.
- **Double-Counting Test Metrics**:
  - Total test metrics were reported by summing Gate 4 (1,709 passed) + Gate 5 (23 passed) + Gate 6 (34 passed) = 1,766 tests, even though Gates 5 and 6 execute a subset of the test suites already present in `tests/`.

---

## 4. Cryptographic Audit (`mdrap-audit`) Specification Gaps

- **Domain Separation Mismatch**:
  - `packages/mdrap-audit/mdrap_audit/__init__.py:66` concatenated ASCII hex string representations rather than raw 32-byte binary hashes (`b"\x01" + (left + right).encode("utf-8")`).
- **RFC 6962 Tree Topology**:
  - Tree construction duplicated the last odd node (`right = left`) instead of splitting at the largest power of two strictly less than $n$ ($k = 2^{\lfloor\log_2(n-1)\rfloor}$), as mandated by RFC 6962 Section 2.1.
- **Consistency Proofs Missing**:
  - `packages/mdrap-audit` implemented `InclusionProof` but lacked `ConsistencyProof` (`SUBPROOF(m, D[n], b)`) between two snapshots of the log.
- **Test Vectors Missing**:
  - No independent test vectors existed to verify bit-exact RFC 6962 compliance against known test vectors (e.g. Certificate Transparency RFC 6962 test vectors).

---

## 5. Summary of Baseline Recovery Action Plan

| Phase & Agent | Focus Area | Planned Remediation Action |
|:---|:---|:---|
| **Agent 1** | Package Architecture | Unignore and track companion packages; integrate `mdrap-strategies` packaging into builds and wheels. |
| **Agent 2** | Cryptographic Integrity | Re-architect `mdrap-audit` to bit-exact RFC 6962 binary domain separation, full tree topology, inclusion & consistency proofs, and independent test vectors. |
| **Agent 3** | Test Infrastructure | Fix Gate 8 -> Gate 4 ordering inversion in `scripts/check.py`; eliminate double-counting; enforce clean-checkout validation. |
| **Agent 4** | Claims Governance | Update `claims_scanner.py` to derive test counts directly from machine-readable pytest JSON output; enforce single authoritative metadata source. |
| **Agent 5** | Dependency Security | Replace incomplete Gate 7 check with `pip-audit` / transitive dependency vulnerability scan with machine-readable report. |
| **Agent 6** | Native C Parity | Differential stress testing of C fastpath vs Python fallback across edge cases, malformed payloads, and SIMD paths. |
| **Agent 7** | Release Packaging | Generate wheel and source distribution including all advertised packages; verify clean venv install; regenerate release ZIP from exact release commit. |
| **Agent 8** | Adversarial Review | Independent adversarial challenge of all remediated components, test integrity, and packaging reproducibility. |

---

## 6. Reproduction Commands

```bash
# 1. Reproduce missing package import failure outside repo root
python -c "import mdrap_strategies"
python -c "import mdrap_audit"

# 2. Reproduce release zip omission
python -c "import zipfile; zf = zipfile.ZipFile('mdrap_v5.3.0_release.zip'); print([f for f in zf.namelist() if 'packages/' in f])"

# 3. Reproduce gate ordering issue on clean checkout
# (Deleting binaries causes Gate 4 to fail before Gate 8 can run)
```
