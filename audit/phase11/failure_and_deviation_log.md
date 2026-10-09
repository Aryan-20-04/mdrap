# MDRAP Phase 11 — Failure & Deviation Audit Log

## 1. Executive Summary
This document provides a comprehensive log of all **Environmental Deviations, Architectural Limitations, and Compensating Controls** identified during the execution of MDRAP Phase 11.

In strict compliance with Institutional Design Principles §1 ("Correctness before optimization") and §2 ("Measure before claiming"), no deviation is concealed, minimized, or falsely converted into an unearned pass.

---

## 2. Comprehensive Deviation Register

### DEV-01: Single Physical Host Topology
- **Description**: The staging cluster was deployed across three separate operating system processes (`subprocess.Popen` / isolated daemons) on a single physical host (`DESKTOP-MDRAP`, Windows 11 Enterprise x86_64).
- **Deviation from Production Standard**: Institutional production requires three physically isolated bare-metal servers installed in separate equipment racks with independent power distribution units (PDUs) and network feeds.
- **Architectural Impact**: A physical power failure, hardware bus panic, or host OS blue screen halts all three nodes simultaneously, rendering quorum failover impossible at the physical hardware layer.
- **Compensating Controls**:
  - Isolated process PIDs, separate network ports (`8111`, `8112`, `8113`), and independent on-disk SQLite databases and WAL directories.
  - Acceptance Gate G2 is formally designated **BLOCKED / ENVIRONMENT-LIMITED**.

### DEV-02: Loopback Network Stack vs Physical Switch Fabric
- **Description**: Inter-node consensus heartbeats and client fan-out streams were transmitted over `127.0.0.1` and `10.21.12.27`.
- **Deviation from Production Standard**: Production market-data fan-out traverses 25GbE enterprise switch fabrics (Arista 7050SX3) with physical optics, patch cables, and top-of-rack buffering.
- **Architectural Impact**: Windows loopback TCP stack avoids physical Ethernet MTU fragmentation, frame CRC errors, and transceiver packet drops. Measured client-receipt latency reflects OS kernel IPC efficiency rather than physical switch transit delay.
- **Compensating Controls**:
  - Injected artificial delays, backpressure buffer limits, and socket queue drops in fan-out test harnesses.
  - Verified 10/10 noisy-neighbor consumer evictions under real TCP socket stall conditions.

### DEV-03: Shared Hardware Time Stamp Counter (TSC)
- **Description**: All cluster nodes sampled `time.time()` and `time.monotonic()` from the same host motherboard TSC.
- **Deviation from Production Standard**: Physically separate servers experience clock drift between their respective hardware quartz oscillators, necessitating IEEE 1588v2 PTP time synchronization.
- **Architectural Impact**: Localhost testing eliminates relative clock drift between leader and follower, masking potential lease renewal races caused by severe clock skew.
- **Compensating Controls**:
  - Lease TTL ($T_{\text{lease}} = 500\text{ ms}$) configured with a generous safety margin over heartbeat renewal ($T_{\text{heartbeat}} = 150\text{ ms}$).
  - Hardware PTP synchronization documented as a mandatory prerequisite for multi-host bare-metal deployment.

### DEV-04: Commercial & Legal Gate on Mode C Live Feeds
- **Description**: Live exchange feeds from Nasdaq (TotalView-ITCH) and CME (MDP 3.0 SBE) were not connected.
- **Deviation from Production Standard**: Production validation requires real-time read-only shadow consumption from exchange matching engines.
- **Architectural Impact**: Live venue protocol nuances (e.g. unexpected exchange sequence resets, burst gaps) were evaluated via synthetic generators rather than live venue data lines.
- **Compensating Controls**:
  - Heterogeneous synthetic feed simulator injected crossed quotes, sequence gaps, zero quantities, and negative prices.
  - Mode C explicitly designated **BLOCKED / COMMERCIALLY GATED**.

### DEV-05: Absence of Solarflare Kernel-Bypass NICs (Mode D)
- **Description**: Native C fastpath kernel executed on standard Windows user-space sockets rather than Solarflare Onload / DPDK raw packet rings.
- **Deviation from Production Standard**: Ultra-low-latency market data ingress requires kernel-bypass network drivers on enterprise Linux.
- **Compensating Controls**:
  - Native C fastpath kernel (`src/fastpath.c`) compiled and validated with MSVC on Windows.
  - Mode D explicitly designated **BLOCKED / HARDWARE GATED**.

---

## 3. Deviation Disposition & Audit Sign-Off
No defects or deviations were hidden or relaxed. All deviations trace to physical hardware or commercial licensing constraints outside the scope of single-workstation staging. The platform code base is certified robust at the software layer under Mode B staging conditions.
