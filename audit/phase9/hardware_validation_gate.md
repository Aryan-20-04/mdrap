# MDRAP Phase 9 — Hardware-Validated Ingress & Kernel-Bypass Gate Report

## 1. Executive Summary
This document establishes the **Hardware Validation Gate** for low-latency kernel-bypass network ingress in MDRAP.

In Phase 8, architecture stubs and kernel-bypass readiness interfaces (`src/ingress.py`, `src/mdrap/ingress.py`, `MulticastIngressEngine`) were implemented to prepare MDRAP for hardware-accelerated network packet processing.

In Phase 9, we audited the physical capabilities of the local host environment (Windows 11 Enterprise x86_64). Because kernel-bypass NIC hardware and specialized kernel drivers are physically absent on this host, **native hardware bypass is strictly designated as GATED / ENVIRONMENT-LIMITED**.

This report establishes the baseline boundaries, details the software emulation verified, and defines the hardware certification plan required for colocation deployment.

---

## 2. Host Hardware Inventory & Capability Audit
Data source: [`audit/phase9/environment_inventory.md`](audit/phase9/environment_inventory.md)

| Subsystem | Local Environment State | Institutional Production Requirement | Variance / Gap |
| :--- | :--- | :--- | :--- |
| **Operating System** | Windows 11 Enterprise (Build 26100) | Red Hat Enterprise Linux 9 / Rocky Linux 9 | Windows does not support Onload / DPDK |
| **Network Interface Card** | Standard Realtek PCIe GbE / Intel Wi-Fi 6 | Solarflare Flareon Ultra X2522 / ExaNIC X10 | Hardware bypass NIC physically absent |
| **Kernel Bypass Drivers** | None (Winsock2 OS network stack) | Solarflare OpenOnload v8.1+ / DPDK 23.11 LTS | Bypass drivers require Linux kernel |
| **Clock Synchronization** | Windows W32Time (SNTP, ~5 ms accuracy) | PTP IEEE 1588 Hardware Timestamping (< 100 ns) | PTP grandmaster clock absent |
| **CPU Core Isolation** | Standard Windows OS thread scheduler | Linux `isolcpus`, `nohz_full`, `rcu_nocbs` | Real-time core pinning unavailable |
| **Memory Architecture** | Unified memory (Standard Windows paging) | Dedicated NUMA Node, 1GB Transparent HugePages | HugePage kernel allocation unavailable |

---

## 3. Emulated Software Validation in Mode A
While physical hardware is absent, MDRAP verified all ingress software abstractions under Mode A using standard POSIX/Winsock sockets:
1. **Multicast Ingress Framing**:
   Ingestion of UDP multicast packets via `MulticastIngressEngine` parsing length-framed payloads without memory allocations.
2. **Zero-Copy Handoff to SHM Ring Buffer**:
   Handoff of network frames directly into the SPSC shared-memory ring buffer (`src/shm.py`, `src/fastpath.c`) verified using seqlock synchronization.
3. **Sequence Monotonicity & Loss Detection**:
   Ingress packet sequence checking correctly identified injected packet loss and sequence inversions.

---

## 4. Hardware Lab Certification Plan (Mode D)
To achieve full production sign-off for sub-microsecond colocation trading, the deployment must pass the following 4-step hardware validation campaign in an authorized Linux testbed:

### Step 1: Operating System & Kernel Tuning
- **Distribution**: Rocky Linux 9.4 (Kernel 5.14.0+ real-time patch).
- **Bootloader Parameters**:
  ```text
  isolcpus=2-15 nohz_full=2-15 rcu_nocbs=2-15 default_hugepagesz=1G hugepagesz=1G hugepages=16 processor.max_cstate=0 intel_idle.max_cstate=0 mce=ignore_ce
  ```
- **NUMA Pinning**: Pin MDRAP ingress thread and Solarflare NIC IRQ to NUMA Node 0.

### Step 2: OpenOnload / EF_VI Network Acceleration
- **Driver**: Solarflare Onload v8.1.3 installed with kernel module `onload.ko`.
- **EF_VI Direct Ingress**: Configure EF_VI zero-copy userspace ring buffer listening directly to exchange multicast groups, completely bypassing the Linux IP stack.
- **Target Ingress Latency**: $\le 450\text{ ns}$ wire-to-memory.

### Step 3: PTP Grandmaster Clock Synchronization
- Connect dedicated PCIe PTP card (Oregano syn1588 or Solarflare PTP hardware clock).
- Synchronize hardware timestamps via `ptp4l` and `phc2sys` against exchange grandmaster clock (e.g. Meinberg LANTIME).
- Validate clock drift $\le 50\text{ ns}$.

### Step 4: Hardware Stress & Soak Run
- Replay 100 million packets from an Spirent / Xena hardware packet generator at line-rate 10Gbps (14.88 Mpps).
- Measure packet drop count (target: 0 drops) and tail latency ($p99.9 \le 1.2\text{ \mu s}$).

---

## 5. Gate Decision
- **Mode A (Software / Emulated Ingress)**: **VERIFIED & OPERATIONAL**
- **Mode D (Physical Hardware Bypass)**: **GATED / ENVIRONMENT-LIMITED**
- **Certification Boundary**: Hardware certification cannot be completed until the system is provisioned on authorized Linux server hardware equipped with Solarflare / ExaNIC adapters.
