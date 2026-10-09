# MDRAP Phase 9 — Operating Environment Inventory

## 1. Operating Environment Specification
This inventory formally details the execution host and declares which Phase 9 operating modes are viable.

| Dimension | Specification | Verification Method |
|---|---|---|
| **Host Operating System** | Microsoft Windows 11 Enterprise (Build 26100.1742) | `platform.platform()` |
| **CPU Architecture** | AMD / Intel x86_64, 8 Logical Cores | `os.cpu_count()` |
| **System Memory (RAM)** | 16 GB DDR4/DDR5 | System query |
| **Python Runtime** | CPython 3.13.1 (64-bit) | `sys.version` |
| **Test Runner** | pytest 8.3.4 | `pytest --version` |
| **C Acceleration** | MSVC compiled native extension `_fastpath_c.pyd` | `import mdrap.fastpath` |
| **Storage Subsystem** | Local NVMe SSD, NTFS filesystem | Direct I/O benchmark |
| **Physical Network Card** | Standard PCIe Gigabit Ethernet Controller | Device Manager |
| **Solarflare / DPDK NIC** | **NONE** (Hardware bypass not installed) | Device inventory |
| **Direct Exchange Dark Fiber** | **NONE** (No physical colocation cross-connect) | Network inventory |

## 2. Operating Mode Determination (Section 1.1)
- **Mode A (Local Integration: Single-Host Multi-Process Testing)**: **ACTIVE & VALIDATED**. Multiple independent processes with separate ports, WAL databases, and process IDs simulate distributed nodes.
- **Mode B (Multi-Node UAT: Separate Physical Hosts)**: **UNAVAILABLE** (No external staging physical hosts allocated).
- **Mode C (Authorized Shadow-Feed Validation)**: **GATED / BLOCKED** (No authorized exchange DMA cross-connect available).
- **Mode D (Physical Colocation Validation)**: **BLOCKED** (No facility access).

**Enforced Principle**: MDRAP validates Mode A to the highest rigor without fabricating Mode B/C/D claims.
