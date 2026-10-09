# Phase 5 Environment Prerequisites & System Requirements

**Platform**: MDRAP v3.0.0  
**Target Architecture**: x86_64 / ARM64  
**Date**: 2026-10-09  

---

## 1. Hardware & System Requirements

| Resource | Minimum (Staging / Pilot) | Recommended (Production 50k eps) |
| :--- | :--- | :--- |
| **CPU** | 2 Physical Cores (x86_64) | 8 Dedicated Cores (Pinning recommended) |
| **Memory** | 2 GB RAM | 8 GB RAM |
| **Storage** | 20 GB SSD | 250 GB NVMe (Direct I/O capable) |
| **Network** | 1 GbE Ethernet | 10/25 GbE SR-IOV NIC (Intel/Mellanox) |

---

## 2. Operating System & Software Prerequisites

1. **Python Runtime**: Python 3.11, 3.12, or 3.13 (CPython standard 64-bit build).
2. **C Toolchain**: MinGW GCC 14+ / MSVC on Windows; GCC 10+ / Clang 12+ on Linux.
3. **Java Runtime**: OpenJDK 17+ (Java 20 tested and validated for Java SDK).
4. **OS Limits**:
   - `ulimit -n 65536` (file descriptors).
   - `sysctl -w net.core.rmem_max=16777216` (socket read buffer ceiling).
