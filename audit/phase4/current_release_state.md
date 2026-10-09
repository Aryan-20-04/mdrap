# MDRAP Phase 4 — Current Release State

**Document Identifier**: `MDRAP-STATE-P4-001`  
**Date**: October 9, 2026  

---

## 1. Release Inventory

- **Release Version**: `3.1.0`
- **Current Git Revision**: `df06865`
- **Supported Topologies**:
  - Profile A: Single-Node Ingestion & Persistence (IngestLog WAL + SQLite + SHM IPC)
  - Profile B: Active-Passive High Availability with Epoch Fencing
- **SDK Availability**:
  - C++17/20 SDK: Production-Supported (validated with GCC 14.x)
  - Java 17+ SDK: Production-Supported (validated with JDK 20)
  - Rust SDK: Beta / Test (source validated; host lacks cargo)
  - Python API / Client: Production-Supported
- **Ingress Backends**:
  - Socket TCP Ingress: Production-Supported
  - WebSocket Ingress: Production-Supported
  - ReplayFeedAdapter: Production-Supported
  - Kernel-Bypass AF_XDP: Deferred / Linux-only
