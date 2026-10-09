# Phase 4 Reproducible Builds & Packaging Report

**Platform Target**: Linux x86_64 / Windows x86_64  
**Python Runtime**: 3.11, 3.12, 3.13  
**Compiler Flags**: GCC/Clang `-O3 -fPIC -Wall -Wextra` / MSVC `/O2`  
**Timestamp**: 2026-10-09  

---

## 1. Build Reproducibility Principles

All release artifacts are compiled with deterministic, reproducible build flags:
1. **C Fastpath Extension**:
   ```bash
   python src/mdrap/build_fastpath.py
   ```
   Uses `-O3` optimization with explicit structure alignment padding and strict ANSI C99 / C++17 compatibility.
2. **Deterministic Source Wheel Packaging**:
   - Built via `python -m build --wheel --sdist`.
   - Excludes non-source artifacts (`.pyc`, `.git`, test databases).
3. **Multi-Platform C-ABI Parity**:
   - Fixed 64-byte SBE layout with fixed little-endian byte ordering guarantees identical memory serialization whether running on Linux, macOS, or Windows.
