# MDRAP Phase 3 — Platform Support Matrix

**Document Identifier**: `MDRAP-SUPPORT-P3-001`  
**Date**: October 9, 2026  

---

## 1. Operating Systems & Hardware Architectures

| Platform | Architecture | Core Engine | C++ SDK | Java SDK | Rust SDK | Kernel Bypass |
|---|---|---|---|---|---|---|
| **Linux (Ubuntu 22.04 / RHEL 9)** | x86-64 | Supported | Supported | Supported | Supported | Experimental (AF_XDP) |
| **Linux (Ubuntu 22.04 / RHEL 9)** | aarch64 | Supported | Supported | Supported | Supported | Experimental (AF_XDP) |
| **Microsoft Windows 10 / 11 / Server** | x86-64 | Supported | Supported | Supported | Supported | Unsupported |
| **macOS (Darwin 13+)** | arm64 / x86-64 | Supported | Supported | Supported | Supported | Unsupported |

---

## 2. Deployment Configurations

- **Single Node**: Production-supported with IngestLog WAL + SQLite + Local SHM IPC.
- **Active-Passive HA**: Production-supported with epoch-based fencing, heartbeat monitoring, and standby sequence catch-up.
- **Multi-Datacenter Distributed Paxos**: Explicitly **Out of Scope** for Phase 3.
