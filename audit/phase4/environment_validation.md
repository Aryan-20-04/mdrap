# Phase 4 Operating Environment Validation

**Target Support Matrix**: Operating systems, kernels, CPU architectures, and Python versions  
**Timestamp**: 2026-10-09  

---

## 1. Supported Environments

| OS / Platform | Architecture | Python Versions | Native Acceleration Status |
| :--- | :--- | :--- | :--- |
| **Linux (RHEL 8/9, Ubuntu 22.04/24.04)** | x86_64 | 3.11, 3.12, 3.13 | **Tier 1 Primary**: Native C + GCC/Clang |
| **Windows 10/11, Windows Server 2022** | x86_64 | 3.11, 3.12, 3.13 | **Tier 1 Supported**: Native MSVC / MinGW |
| **Linux aarch64 (AWS Graviton)** | aarch64 / ARM64 | 3.11, 3.12, 3.13 | **Tier 2 Supported**: GCC/Clang portable C |
| **macOS (Darwin)** | arm64 (Apple Silicon) | 3.11, 3.12, 3.13 | **Development Profile**: Local simulation |

---

## 2. Kernel & System Tunables (Production Linux)

For production deployments processing > 50,000 events/sec:
1. **Network Buffer Sizing**:
   ```bash
   sysctl -w net.core.rmem_max=16777216
   sysctl -w net.core.wmem_max=16777216
   ```
2. **File Descriptors**:
   ```bash
   ulimit -n 65536
   ```
3. **Hugepages**:
   - 2MB Hugepages recommended for zero-copy SHM ring buffers.
