# MDRAP Phase 3 — SDK Compatibility Matrix

**Document Identifier**: `MDRAP-SDKCOMPAT-P3-001`  
**Date**: October 9, 2026  

---

## 1. Supported Language & Runtime Matrix

| Language | Minimum Supported Version | Recommended Version | OS Support | Toolchains Verified |
|---|---|---|---|---|
| **C++** | C++17 | C++20 | Linux (glibc 2.28+), Windows 10/11, macOS 12+ | GCC 11+, Clang 13+, MSVC 2019+ (MinGW GCC 14.x verified) |
| **Java** | Java 17 (LTS) | Java 21 (LTS) | Linux, Windows, macOS | OpenJDK 17+, Oracle JDK 20+ (JDK 20 verified) |
| **Rust** | Rust 1.70.0 | Rust 1.78.0+ | Linux, Windows, macOS | `cargo` (Compilation deferred on host; source validated) |
| **Python**| Python 3.10 | Python 3.13 | Linux, Windows, macOS | CPython 3.10–3.13 (Python 3.13.1 verified) |
