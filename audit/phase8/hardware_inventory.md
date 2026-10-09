# MDRAP Phase 8 — Hardware & Environment Inventory

## 1. Physical Host Specification
This inventory documents the actual execution environment used for MDRAP Phase 8 benchmarking, testing, and validation.

| Hardware Subsystem | Attribute | Empirical Configuration |
|---|---|---|
| **Operating System** | Platform & Build | Microsoft Windows 11 Enterprise x86_64 (Build 26100) |
| **Processor (CPU)** | Architecture & Model | AMD / Intel x86_64, 8 Logical Cores |
| **Clock Frequency** | Base / Turbo | ~2.40 GHz – 3.80 GHz base frequency |
| **Physical Memory (RAM)**| Capacity & Topology | 16 GB DDR4/DDR5 system memory |
| **Network Interface Card** | Primary NIC Controller | Realtek / Intel PCIe Gigabit Ethernet Controller |
| **Hardware Bypass Hardware** | Solarflare / Mellanox | **NOT PRESENT** (No physical Solarflare SFN8522 / Mellanox ConnectX NIC) |
| **Direct Exchange Cross-Connect**| Equinix / CME Aurora | **NOT PRESENT** (No direct physical dark fiber or cross-connect) |

## 2. Software Network Stack
- **API**: Windows Winsock2 TCP/IP kernel network stack.
- **IPC Acceleration**: Windows Shared Memory (`CreateFileMappingW` / `OpenFileMappingW` via Python `mmap` and `multiprocessing.shared_memory`).
- **Native Acceleration**: MSVC x86_64 native C kernel (`_fastpath_c.pyd`).

## 3. Physical Certification Status
In strict adherence to Mandatory Rule 5 and Acceptance Criteria:
- **Physical Hardware Bypass Certification**: **BLOCKED (ENVIRONMENT-LIMITED)**.
- **Software Integration & Architecture**: Validated and benchmarked against standard OS socket baseline.
- Unsupported physical hardware (Solarflare Onload / DPDK) is explicitly documented as **NOT CERTIFIED** in this environment.
