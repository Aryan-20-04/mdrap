# MDRAP Phase 3 — Current Capability Matrix

**Document Identifier**: `MDRAP-CAP-P3-001`  
**Date**: October 9, 2026  

---

## 1. Capability Status Matrix at Phase 3 Ingress

| Subsystem | Area | Current Status | Production Guarantee | Notes |
|---|---|---|---|---|
| **Core Ingestion** | SBE, JSON, ITCH 5.0 | Implemented (C & Python) | Supported | IngestLog WAL + C fastpath |
| **Persistence** | IngestLog (WAL) + SQLite | Hardened in Phase 1 | Supported | Atomic flush, CRC32, WAL journal mode |
| **Runtime Control** | State FSM, Supervision | Hardened in Phase 2 | Supported | Bounded queues, degraded state alerts |
| **Consumer Interfaces**| Python API, FastAPI, WS | Implemented | Supported | REST & WebSocket live streaming |
| **C++ Consumer SDK** | Native C++17/20 Client | Not implemented | Planned (Phase 3) | Target: Zero-copy IPC / TCP consumer |
| **Rust Consumer SDK** | Native Rust Client | Not implemented | Planned (Phase 3) | Target: Idiomatic safe client |
| **Java Consumer SDK** | Java 17+ Consumer | Not implemented | Planned (Phase 3) | Target: AutoCloseable socket/IPC client |
| **Feed Ingress Adapters**| Pluggable Ingress FSM | Ad-hoc in `src/gateway.py` | Planned (Phase 3) | Target: Formal `FeedAdapter` lifecycle |
| **Kernel Bypass** | AF_XDP / DPDK | Unimplemented | Evaluation (Phase 3) | Hardware / OS requirements assessment |
| **Compliance & Metering**| Entitlements & Metering | Basic API tokens | Planned (Phase 3) | Target: Durable accounting & usage export |
| **High Availability** | Active-Passive Failover | Basic `failover.py` shim | Planned (Phase 3) | Target: Fencing tokens, epoch progression |
| **Experimental Modules**| Vessel, Options, News | Exploratory | Experimental | Explicitly segregated from core engine |
