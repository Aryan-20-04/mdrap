# MDRAP Phase 3 — Native SDK Architecture Specification

**Document Identifier**: `MDRAP-SDK-P3-001`  
**Date**: October 9, 2026  
**Status**: APPROVED & IMPLEMENTED  

---

## 1. Multi-Language SDK Strategy

To support algorithmic trading systems, quant analytics, and enterprise data distribution, MDRAP provides native consumer libraries in C++, Java, and Rust:

| Language | Primary Target | Standard | Transport Layer | Memory Model |
|---|---|---|---|---|
| **C++** | Low-latency market-making / execution engines | C++17 / C++20 | Shared Memory / TCP Socket | Zero-copy borrowed `const` view, RAII lifecycle |
| **Java** | Quantitative research, analytics pipelines, OMS | Java 17+ / JDK 20 | TCP Sockets / ByteBuffer | `record` models, `AutoCloseable` lifecycle |
| **Rust** | High-throughput concurrent microservices | Rust 2021 | TCP Sockets / SBE Slice | Zero-copy slices, safe error handling |

---

## 2. Binary Framing Protocol (SBE 64-byte Layout)

All three SDKs consume the identical 64-byte little-endian memory layout defined in the canonical event contract:
- `instrument` (16 bytes null-padded ASCII)
- `sequence` (uint64)
- `exchange_ts_ns` (int64)
- `price` (double)
- `quantity` (double)
- `event_type` (uint32)
- `quality_flag` (uint32)
- `padding` (8 bytes reserved)

---

## 3. Conformance & Verification Status

- **C++ SDK** (`sdk/cpp/`): Successfully compiled with MinGW `g++` (`C:\msys64\mingw64\bin\g++.exe`), example verified passing all assertions (`sdk/cpp/examples/consumer_example.exe`).
- **Java SDK** (`sdk/java/`): Successfully compiled with Oracle JDK 20 (`javac.exe`), standalone example verified executing with 100% assertions passing.
- **Rust SDK** (`sdk/rust/`): Complete crate delivered (`Cargo.toml`, typed structs, safe consumer). Host environment lacks `cargo`/`rustc`; compilation is deferred until cargo is provisioned.
