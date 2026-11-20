# MDRAP 6.0.0 — Build, Packaging & PEP 427 Conformance Audit

**Audit Date:** November 2026  
**Auditor:** Principal Release & Packaging Systems Engineer  
**Target:** Wheel Distributions (`dist/*.whl`), Source Tarballs (`dist/*.tar.gz`), and Build System Configuration  

---

## 1. Executive Summary

This audit assesses the reproducibility, standards compliance, and platform portability of MDRAP 6.0.0 packages. The primary objective was verifying compliance with **PEP 427 (The Wheel Binary Package Format 1.0)**, ensuring pure Python wheels do not bundle platform-specific compiled binaries.

---

## 2. Distribution Package Inspection

All distribution files in `dist/` were unzipped and inspected for binary contamination:

| Distribution Archive | Tag Classification | Contains Native Binaries? | PEP 427 Compliant? | Defect Classification |
| :--- | :--- | :--- | :--- | :--- |
| `mdrap_core-6.0.0-py3-none-any.whl` | Pure Python (`none-any`) | **YES (`mdrap/_fastpath_native.dll`)** | **NON-COMPLIANT** | **P1 (Active Leak)** |
| `mdrap_compat-4.0.1-py3-none-any.whl` | Pure Python (`none-any`) | None | Compliant | Clean |
| `contrib-4.0.1-py3-none-any.whl` | Pure Python (`none-any`) | None | Compliant | Clean |
| `mdrap_core-6.0.0.tar.gz` | Source Distribution | Source C files only | Compliant | Clean |

### Proof of Binary Contamination
From `audit/v6.0.0/raw/forensic_benchmarks.json`:
```json
{
  "filename": "mdrap_core-6.0.0-py3-none-any.whl",
  "binaries_present": [
    "mdrap/_fastpath_native.dll"
  ],
  "is_pure_python_tag": true,
  "violates_pep427": true
}
```

---

## 3. Root Cause Analysis

Commit `3ec4237` attempted to resolve the wheel leak by deleting explicit package-data directives from `pyproject.toml`.
However, under standard `setuptools.build_meta`, any file residing inside `src/mdrap/` is automatically packaged into the wheel unless explicitly excluded!
Because a developer or local test run compiled `src/mdrap/_fastpath_native.dll` directly into the source tree, `build` picked up the un-git-tracked DLL and packaged it into `py3-none-any.whl`.

### Institutional Consequences of PEP 427 Violation
1. **Linux / macOS Installation Failure:** When a Linux client runs `pip install mdrap_core-6.0.0-py3-none-any.whl`, pip assumes the wheel is platform-independent. If the package attempts to load `_fastpath_native.dll` via `ctypes.CDLL`, Linux immediately fails with `OSError: [Errno 8] Exec format error` or `cannot open shared object file`.
2. **Repository Hygiene:** Builds are non-deterministic and depend on whether local developers ran a C compiler before invoking `python -m build`.

---

## 4. Packaging Remediation Strategy

To achieve 100% deterministic, compliant wheel packaging:
1. **Exclude Native Binaries in `pyproject.toml`:**
   ```toml
   [tool.setuptools.package-data]
   mdrap = ["*.json", "*.md"]
   exclude = ["*.dll", "*.so", "*.dylib", "*.pyd"]
   ```
2. **Add `MANIFEST.in`:**
   ```
   global-exclude *.dll *.so *.dylib *.pyd
   recursive-include src/mdrap *.py *.c *.h
   ```
3. **Dedicated Build Step for Platform Wheels:** Compile C extensions exclusively into platform-specific wheels (e.g. `mdrap_core-6.0.0-cp313-cp313-win_amd64.whl` and `manylinux2014_x86_64.whl`), leaving `py3-none-any.whl` strictly binary-free.
