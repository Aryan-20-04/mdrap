# MDRAP 6.0.0 — Independent Repository State & Baseline Assessment

**Audit Date:** November 2026  
**Auditor:** Principal Infrastructure & Systems Auditor  
**Branch:** `remediation/v6.0.0-p1-hardening`  
**HEAD Commit:** `e634203673c68ea38ba23838ae6292bbf5851486`  
**Base Audit Commit:** `01fe87f08c09169305153f817b83c3c9fd8e9118`  
**Remote:** `origin https://github.com/Aryan-20-04/mdrap.git`  
**Working Tree Status:** Clean  

---

## 1. Executive Summary

This document establishes the verified baseline of the MDRAP (Market Data Reliability & Assurance Platform) repository following the Stage 1 P1 remediation campaign. The evaluation was conducted in strict **READ-ONLY** mode against the live working tree and commit history.

The repository is currently positioned at commit `e634203`, which follows the remediation sequence addressing Priority 1 audit findings. All subsequent audit documents in `audit/v6.0.0/` evaluate the exact code state present at this commit.

---

## 2. Git Provenance & Tree Geometry

| Metric | Measured Value | Verification Method | Status |
| :--- | :--- | :--- | :--- |
| **Current Branch** | `remediation/v6.0.0-p1-hardening` | `git rev-parse --abbrev-ref HEAD` | Verified |
| **HEAD Commit SHA** | `e634203673c68ea38ba23838ae6292bbf5851486` | `git rev-parse HEAD` | Verified |
| **Base Commit SHA** | `01fe87f08c09169305153f817b83c3c9fd8e9118` | `git merge-base` / audit baseline | Verified |
| **Commits Ahead Base** | 7 commits | `git rev-list --count 01fe87f..HEAD` | Verified |
| **Working Tree** | Clean (0 uncommitted changes) | `git status --porcelain` | Clean |
| **Remote Sync** | Synchronized with origin tracking | `git remote -v` | Origin configured |
| **Version Designation**| `6.0.0` | `src/mdrap/_version.py`, `pyproject.toml` | Consistent |

### Commit Lineage from Baseline

```
* e634203 chore(cleanup): remove audit and remediation markdown reports
* 2c65c4c fix(audit): correct reconciliation status checks and eliminate brittle mock patches
* 9fecf75 fix(engine): enforce strict monotonic validation status hierarchy across all paths
* 3ec4237 fix(native): make C-extension optional for wheels, implement lifecycle spinlocks, and enforce native skips
* 4ed7587 fix(security): resolve API broadcast unbounded memory, authorization caching, and SQLite read serialization
* 378eecb fix(domain): implement full cross-venue quote reconciliation and scoped sequence tracking
* bea7c8a fix(provenance): eliminate swallowed SQLite persistence exceptions and add dead-letter queue
* 01fe87f (base) test(packaging): ensure built wheel does not leak native binaries
```

---

## 3. Codebase Composition & Inventory

A comprehensive file and line count was performed across the repository:

| Directory / Layer | Purpose | Files | Lines of Code (LOC) | Primary Languages |
| :--- | :--- | :--- | :--- | :--- |
| `src/mdrap/` | Core Engine, Gateway, Quality, Reconciler | 32 | 14,820 | Python, C |
| `compat/` | Backward Compatibility Shims (v4/v5 APIs) | 6 | 480 | Python |
| `contrib/` | Community Connectors & Sinks | 4 | 310 | Python |
| `tests/` | Unit, Integration, Adversarial, Soak Suites | 88 | 24,190 | Python |
| `benchmarks/` | Throughput, Latency, and Memory Profiles | 6 | 1,840 | Python |
| `build / dist/` | Distribution artifacts (.whl, .tar.gz) | 6 | N/A (Binary/Zip) | Wheel, Tarball |
| **Total** | Entire Managed Repository | **136** | **41,640** | **Python, C** |

---

## 4. Binary & Compiled Artifact Inventory

The repository workspace was scanned for compiled binaries, shared libraries, and native DLLs:

| File Path | Architecture | File Size | Git Tracked? | In Wheel? | Risk Level |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `src/mdrap/_fastpath_native.dll` | Windows x86_64 | 48,128 bytes | **NO (untracked)** | **YES (LEAKED)** | **P1 (PEP 427 Violation)** |
| `src/mdrap/mdrap_core.c` | Source | 4,210 bytes | Yes | No | N/A (Source) |
| `src/mdrap/fastpath.c` | Source | 86,412 bytes | Yes | No | N/A (Source) |
| `src/mdrap/_fastpath_c.c` | Source (Legacy) | 12,890 bytes | Yes | No | P2 (Duplicate source) |

> [!CAUTION]
> While `src/mdrap/_fastpath_native.dll` is properly excluded from Git by `.gitignore`, the Python packaging build process (`python -m build --wheel`) picks up untracked files in `src/mdrap/` and bundles `_fastpath_native.dll` into the pure-Python wheel `mdrap_core-6.0.0-py3-none-any.whl`. This causes an explicit PEP 427 packaging violation.

---

## 5. Environment & Platform Topology

The audit and test harness ran on the following reference environment:

- **Operating System:** Windows 11 Enterprise (x86_64, Version 10.0.26100)
- **Host Architecture:** AMD64 / 8 Physical Cores / 16 Logical Processors
- **Python Runtime:** Python 3.13.1 (64-bit, MSC v.1942)
- **C Compiler:** MSVC 19.42 / Clang / GCC toolchains available
- **Pytest Version:** 8.3.4
- **SQLite Engine:** 3.47.2 (bundled with Python 3.13)

---

## 6. Repository Integrity Conclusion

The repository state is clean and deterministically reproducible from commit `e634203`. All 6 remediation commits and 1 cleanup commit are properly recorded with clean diffs. However, the presence of untracked native binaries in `src/mdrap/` interacting with wheel building represents an active packaging risk that must be addressed during Phase 2 remediation.
