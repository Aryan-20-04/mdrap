# MDRAP Phase 6 — Platform Support Matrix, Compatibility Tiers, and Lifecycle Status

## 1. Executive Summary & Policy Scope
To provide clarity to institutional trading desks, quantitative researchers, and operations teams, this document defines the official **Platform Support Matrix** for MDRAP Phase 6.

Capabilities are categorized into three explicit lifecycle tiers:
- **Tier 1: Production-Supported**: Fully tested, validated under benchmarks, SLA-backed, zero known blocking issues.
- **Tier 2: Experimental / Preview**: Functional in test environments; not certified for live production trading without waiver.
- **Tier 3: Deprecated / Scheduled for Removal**: Legacy modules marked for Phase 7 extraction into standalone libraries.

---

## 2. Infrastructure & Runtime Support Matrix

| Dimension | Supported (Tier 1 Production) | Experimental (Tier 2 Preview) | Unsupported / Barred |
| :--- | :--- | :--- | :--- |
| **Operating Systems** | Linux (Ubuntu 22.04+, RHEL 9+), Windows Server 2022 / Windows 11 | macOS (Darwin ARM64 / x86-64) | 32-bit Operating Systems |
| **CPU Architecture** | x86-64 (AVX2 supported), ARM64 (aarch64) | PowerPC, RISC-V | Non-little-endian architectures |
| **Python Runtimes** | CPython 3.11, 3.12, 3.13 | PyPy 3.10 | Python $\le 3.10$ |
| **C Compilers** | GCC 11+, Clang 14+, MSVC 19.30+ | Intel oneAPI C++ | Legacy C89 compilers |
| **Storage Engines** | Local NVMe SSD (XFS / ext4 / NTFS) | AWS EBS gp3 / io2 | Network NFS / CIFS on hot WAL path |
| **IPC Transports** | POSIX / Win32 Shared Memory, TCP Sockets | Solarflare Onload (Kernel-bypass) | Unbounded Named Pipes |

---

## 3. Client Consumer SDK Support Matrix

| Consumer SDK | Transport / Protocol | Maturity Level | Test Coverage | Certified Throughput |
| :--- | :--- | :--- | :--- | :--- |
| **Python SDK** | TCP SBE / SHM / REST / WS | **Tier 1 (Production)** | 100% automated pytest | Up to 25,000 eps |
| **C++ Consumer SDK** | Zero-copy SBE over TCP / SHM | **Tier 1 (Production)** | Native C/C++ harness | $> 500,000\text{ eps}$ (Hot path) |
| **Rust Consumer SDK** | Zero-copy SBE over TCP | **Tier 2 (Preview)** | Cargo integration suite | $> 400,000\text{ eps}$ |
| **Java Consumer SDK** | Agrona DirectByteBuffer SBE | **Tier 2 (Preview)** | JUnit integration suite | $> 350,000\text{ eps}$ |

---

## 4. Platform Module Lifecycle & Feature Classification

```
┌────────────────────────────────────────────────────────────────────────┐
│                      Tier 1: Production-Supported                      │
│                                                                        │
│ • Symbol Partitioning & Sharding (src/partition.py)                    │
│ • Decoupled Bounded Fan-Out (ConsumerFanoutManager)                    │
│ • Multi-Tenant Quota Governance (TenantQuotaManager)                   │
│ • Quality Validation & Rule Engine (src/quality.py)                    │
│ • Multi-Feed Reconciliation (src/reconciliation.py)                    │
│ • IngestLog Write-Ahead Log (src/journal.py)                           │
│ • SPSC Seqlock Shared Memory (src/shm.py)                              │
│ • Native C Hot Path Kernel (src/fastpath.c)                            │
│ • SQLite Batch Storage Drainer (src/storage.py)                        │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      Tier 2: Experimental / Preview                    │
│                                                                        │
│ • Hardware Kernel-Bypass Ingress (src/ingress_kernel_bypass.py)        │
│ • Multi-Region Asynchronous WAN Replication (src/replication.py)       │
│ • Live WebSocket Ingress Feeds (src/ws_feed.py)                        │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 Tier 3: Deprecated (Phase 7 Extraction)                │
│                                                                        │
│ • Monolithic Single-Instance Pipeline (src/pipeline.py)                │
│ • Non-Core TCA Analytics (src/tca.py)                                  │
│ • Non-Core Options Pricing Engine (src/options.py)                     │
│ • Legacy Strategy SDK (src/strategy_sdk.py)                            │
└────────────────────────────────────────────────────────────────────────┘
```
