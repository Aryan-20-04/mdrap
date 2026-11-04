# MDRAP v4.0.1 — Source and Commit Verification Report

**Audit Date:** 2026-11-04  
**Auditor:** Principal Systems Engineer & Independent Release Auditor  
**Branch:** `v4.0.1-final-correctness`  
**Base Commit:** `0e8eef3` (`feat(v4.0.1): recovery, projection, sequence and durability correctness`)  
**Audit Target Commit:** `76374c4` (`docs: publish final correctness audit evidence`)

---

## 1. Executive Summary & Working Tree Baseline

An independent source audit was conducted against the MDRAP v4.0.1 repository. Every remediation commit was inspected for structural integrity, logical separation, commit messages, and content claims.

### Baseline Status
- **Git Branch:** `v4.0.1-final-correctness`
- **Working Tree:** Clean (zero untracked or uncommitted changes prior to audit scripts)
- **Toolchain:** CPython 3.13.1 / 3.12.8, MSVC x64 C Compiler (`cl.exe`), Git 2.47
- **Host OS:** Windows 11 Enterprise (Build 26100) x86_64

---

## 2. Six Remediation Commits Audit

The table below summarizes the six reported commits above `0e8eef3`:

| Commit Hash | Author | Date | Subject | Files Changed | Insertions / Deletions | Audit Verification Verdict |
|---|---|---|---|---|---|---|
| `f51d8f5` | Aryan-20-04 | 2026-11-02 | `fix: recover projection work after queue overflow` | 2 files (`projection.py`, `service.py`) | +315 / -78 | **VERIFIED:** Implements `ProjectionTracker` degraded status, WAL catchup replay, and queue overflow drainage without `BufferError`. |
| `ec5814c` | Aryan-20-04 | 2026-11-02 | `fix: define WAL acknowledgement and durability semantics` | 1 file (`ingestlog.py`) | +13 / -1 | **VERIFIED:** Implements `durability_guarantee` contract strings, barrier flush checks, and OS buffer receipts. |
| `79bb21b` | Aryan-20-04 | 2026-11-02 | `fix: harden stale sequence tracking and session resets` | 2 files (`fastpath.c`, `quality.py`) | +74 / -5 | **VERIFIED WITH FINDING:** Adds 64-bit window bitmask and circular `stale_seen` buffer. *Audit Finding:* C allocated 4 slots whereas Python allocated 64, creating a semantic divergence under adversarial loads. |
| `dc9d72f` | Aryan-20-04 | 2026-11-02 | `fix: make snapshot retention crash-safe` | 1 file (`engine.py`) | +288 / -55 | **VERIFIED:** Implements N-generational snapshot retention (`min_wal_snapshots_retained=2`), fallback discovery, and atomic tempfile replacement. |
| `14b84ea` | Aryan-20-04 | 2026-11-02 | `test: reconcile suite and add adversarial coverage` | 26 files (`tests/final/*`, configs) | +2341 / -23 | **VERIFIED:** Adds 62 adversarial tests across durability, multi-corruption, queue recovery, and sequence matrices. |
| `76374c4` | Aryan-20-04 | 2026-11-02 | `docs: publish final correctness audit evidence` | 19 files (`audit/v4.0.1-final/*`) | +1083 / 0 | **VERIFIED WITH FINDING:** Publishes 15 audit reports and 4 raw test logs. *Audit Finding:* `claims_manifest.json` recorded 1553 selected / 47 deselected from a previous intermediate test run rather than the final 1578 selected / 145 deselected. |

---

## 3. Package Metadata and Version Alignment Audit

A cross-check of version definitions across all repository entry points and package descriptors was performed using [`check_versions.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/reproduction_scripts/check_versions.py):

| Component | Target File | Stated Version | Status |
|---|---|---|---|
| Core Python Engine | `src/mdrap/_version.py` | `4.0.1` | **MATCH** |
| PyPI Packaging Spec | `pyproject.toml` | dynamic (`mdrap._version.__version__`) | **MATCH** |
| Compatibility Shim | `compat/pyproject.toml` | `4.0.1` | **MATCH** |
| Contributor Shim | `contrib/pyproject.toml` | `4.0.1` | **MATCH** |
| Rust SDK Spec | `sdk/rust/Cargo.toml` | `4.0.1` | **MATCH** |
| Documentation Badge | `README.md` | `4.0.1` | **MATCH** |
| Changelog Section | `CHANGELOG.md` | `[4.0.1] - 2026-11-02` | **MATCH** |
| C Accelerator ABI | `src/mdrap/fastpath.c` | `FASTPATH_ABI_VERSION = 5` | **MATCH** |
| Native FFI Checker | `src/mdrap/fastpath.py` | `abi_ver == 5` | **MATCH** |

---

## 4. Discrepancies and Findings

### Finding S-01: Discrepancy in `claims_manifest.json` Test Counts
- **Severity:** P2 (Documentation Hygiene)
- **Confidence:** Confirmed
- **Evidence:** `audit/v4.0.1-final/claims_manifest.json` line 34:
  `"release_gate_stage_5_selected": 1553, "release_gate_stage_5_deselected": 47`
  Actual Gate 5 test execution in [`raw_test_evidence/release_gate.log`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final/raw_test_evidence/release_gate.log):
  `1578 passed, 145 deselected in 308.95s`
- **Impact:** Documentation drifted from actual gate output when 25 additional tests were incorporated into the test suite.
- **Remediation:** Reconciled in `TEST_SUITE_RECONCILIATION.csv` and documented in final independent audit report.
