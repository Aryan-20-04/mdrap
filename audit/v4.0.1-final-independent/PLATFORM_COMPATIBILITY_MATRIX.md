# MDRAP v4.0.1 — Platform and Environment Compatibility Matrix

**Audit Date:** 2026-11-04  
**Auditor:** Principal Systems Engineer & Independent Release Auditor

---

## 1. Operating System Compatibility & Certification Status

| Operating System | Architecture | Toolchain / Compiler | Certification Status | Verified Invariants |
|---|---|---|---|---|
| **Windows 11 / Server 2022** | `x86_64` | MSVC 19.42 (`cl.exe`), CPython 3.12 / 3.13 | **CERTIFIED** | 14/14 Release Gates Passed; Native C Fastpath compiled and verified; NTFS atomic renames verified. |
| **Linux (Ubuntu 22.04 / RHEL 9)** | `x86_64` / `aarch64` | GCC 11+ / Clang 14+, CPython 3.10-3.13 | **UNVERIFIED (Host Offline)** | Code paths use standard POSIX (`_fsync_dir`, `shm_open`), but live tests were not executed on Linux in this audit run. |
| **macOS (Sonoma / Sequoia)** | `Apple Silicon (arm64)` | Apple Clang 15+, CPython 3.10-3.13 | **UNVERIFIED (Host Offline)** | Code paths use standard Darwin APIs, but live tests were not executed on macOS in this audit run. |

> [!WARNING]
> Because live execution was restricted to a Windows 11 host, production readiness is certified strictly for Windows x86_64 environments. Deployments onto Linux or macOS clusters must complete on-host release gate execution before entering production trading.

---

## 2. Python Runtime Compatibility

| Python Version | Status | Notes |
|---|---|---|
| **CPython 3.10** | Supported | Meets minimum `requires-python = ">=3.10"` declared in `pyproject.toml`. |
| **CPython 3.11** | Supported | Verified syntax and stdlib compatibility. |
| **CPython 3.12** | **Verified & Certified** | Baseline testing platform. |
| **CPython 3.13** | **Verified & Certified** | Active audit execution platform (Python 3.13.1). |
| **PyPy** | Unsupported | `ctypes` and native struct marshaling are tuned for CPython C-API layout. |

---

## 3. Acceleration & Fallback Compatibility

| Mode | Trigger | Status | Performance Profile |
|---|---|---|---|
| **Native C Fastpath** | Default (`_NATIVE_LIB` loaded) | **Certified** | ~12 ns quality kernel; struct-by-pointer FFI. |
| **Pure Python Fallback** | `$env:MDRAP_DISABLE_FASTPATH="1"` or DLL load failure | **Certified** | 100% semantic parity with C; ~2.4 µs quality evaluation. |

---

## 4. Ecosystem & SDK Version Alignment

- **Python Core:** `mdrap-core` v4.0.1
- **Python Shims:** `mdrap-compat` v4.0.1, `mdrap-contrib` v4.0.1
- **Rust SDK:** `mdrap-rs` v4.0.1 (`sdk/rust/Cargo.toml`)
- **ABI Version:** Native Fastpath ABI v5 (`fastpath_engine_abi_version() == 5`)
