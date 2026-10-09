# MDRAP Phase 11 — Independent-Host Staging Topology & Environmental Boundary Specification

## 1. Executive Summary & Objective
Phase 11 of the MDRAP engineering program focuses on **Independent-Host Staging, Distributed Safety Certification, and Production-Readiness Gap Closure**. 

The core limitation identified in Phase 10 was:
> All tested TCP endpoints used `127.0.0.1` on a single physical host (`DESKTOP-MDRAP`, Windows 11 Enterprise x86_64). The evidence therefore does not establish physical or virtual multi-host behavior across independent network interfaces and failure domains.

This document formally specifies the **independent-host staging topology required for production certification**, audits the current environment's topological boundaries, and maps the exact gap between local multi-process staging and institutional production multi-host deployment.

---

## 2. Taxonomy of Distributed Testing Environments

In compliance with Phase 11 Mandatory Rules §1.5, §1.6, and §1.7, MDRAP testing environments are strictly classified into five distinct tiers:

| Tier | Designation | Processes | Network Stack | Kernel / OS | Hardware / Failure Domain | MDRAP Status |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|
| **T0** | **In-Process Multi-Threaded** | Single OS Process | In-memory queues / loops | Shared single process space | Shared memory, single PID | Phase 9 Baseline |
| **T1** | **Local Multi-Process (Loopback)** | Distinct OS PIDs | Loopback TCP (`127.0.0.1`) | Shared Windows Kernel | Single Physical Host, shared RAM/CPU | Phase 10 Mode B |
| **T2** | **Local Multi-Process (LAN IP)** | Distinct OS PIDs | Host NIC IP (`10.21.12.27`) | Shared Windows Kernel | Single Physical Host, shared NIC hardware | Phase 10 / 11 Mode B |
| **T3** | **Isolated Virtual Machines / Containers**| Distinct OS PIDs | Virtual Switch / vNICs | Independent Guest Kernels | Shared Hypervisor Host, shared CPU/RAM bus | Target Lab Staging |
| **T4** | **Independent Physical Bare-Metal Hosts** | Distinct OS PIDs | Physical Switch Fabric | Independent Physical Kernels | Genuinely Isolated Physical Hardware (Chassis/PDU) | Production Prerequisite |

> [!CAUTION]
> **Mandatory Rule Enforcement**:
> Running distinct processes communicating over `127.0.0.1` or `10.21.12.27` on a single Windows 11 machine is a **Tier 1 / Tier 2 Local Multi-Process deployment**. It is **NOT** a Tier 4 Independent Physical Multi-Host deployment. Under no circumstances may Tier 1/2 results be represented as physical multi-host proof.

---

## 3. Physical Failure Domain Analysis (Single-Host vs Multi-Host)

| Failure Dimension | Single-Host Execution (Current Localhost) | True Independent-Host Deployment (Target Tier 4) | Impact on High Availability (HA) |
|:---|:---|:---|:---|
| **Host Power Loss** | All 3 nodes (`node-01`, `node-02`, `node-03`) crash simultaneously. Zero quorum survivors. | Independent power supplies, dual A/B power feeds, separate PDUs and UPS units. | In single-host, power loss is fatal. In multi-host, $N-1$ power loss leaves quorum intact. |
| **Kernel Crash / BSOD / Panic** | OS crash takes down all node processes, memory spaces, and sockets instantly. | Kernel crash is strictly isolated to the affected physical machine. | In single-host, 100% cluster loss. In multi-host, surviving nodes detect lease expiry in 105 ms. |
| **CPU / Memory Contention** | Processes share physical cores, L3 cache, memory controller, and memory bus. | Dedicated NUMA nodes, dedicated DDR5 memory channels, independent L3 caches per host. | High memory load on primary can degrade standby in single-host; multi-host provides complete isolation. |
| **Network Interface Card (NIC) Failure** | Virtual loopback or single physical Wi-Fi/NIC driver failure drops all node connectivity. | Redundant dual-port 25GbE PCIe NICs bonded with LACP across dual top-of-rack (ToR) switches. | Single-host NIC reset disrupts entire cluster; multi-host provides link failover. |
| **Clock Drift & PTP Synchronization** | All processes query `time.monotonic()` and `time.time()` from the same host OS TSC. | Independent hardware clocks subject to relative drift, requiring PTP (IEEE 1588v2) / PPS. | Single-host masks clock skew; multi-host requires strict monotonic epoch fencing against clock drift. |

---

## 4. Institutional Independent-Host Target Architecture (Tier 4)

For MDRAP to receive institutional production authorization, the physical staging cluster must match the following reference specification:

```
                      INSTITUTIONAL TIER 4 PRODUCTION TOPOLOGY
                      =========================================

                       ┌───────────────────────────────────────┐
                       │   Downstream Trading Engines (Algo)   │
                       └───────────────────┬───────────────────┘
                                           │ Dual 10GbE Fan-Out
                                           ▼
             ═════════════════════ Core Switch A (Arista 7050SX3) ═════════════════════
             ═════════════════════ Core Switch B (Arista 7050SX3) ═════════════════════
                         │                             │                            │
              Bonded 25G │                  Bonded 25G │                 Bonded 25G │
                         ▼                             ▼                            ▼
             ┌───────────────────────┐     ┌───────────────────────┐    ┌───────────────────────┐
             │       HOST-01         │     │       HOST-02         │    │       HOST-03         │
             │   (Primary Leader)    │     │    (Sync Standby)     │    │  (Quorum Tie-Breaker) │
             ├───────────────────────┤     ├───────────────────────┤    ├───────────────────────┤
             │ Dell PowerEdge R660   │     │ Dell PowerEdge R660   │    │ Dell PowerEdge R660   │
             │ Dual Xeon Gold 6430   │     │ Dual Xeon Gold 6430   │    │ Dual Xeon Gold 6430   │
             │ 64GB DDR5 ECC RAM     │     │ 64GB DDR5 ECC RAM     │    │ 64GB DDR5 ECC RAM     │
             │ Dual NVMe U.2 (WAL)   │     │ Dual NVMe U.2 (WAL)   │    │ Dual NVMe U.2 (WAL)   │
             │ Solarflare X2522 NIC  │     │ Solarflare X2522 NIC  │    │ Solarflare X2522 NIC  │
             │ PTP Hardware Sync     │     │ PTP Hardware Sync     │    │ PTP Hardware Sync     │
             │ Rack 01, PDU A/B      │     │ Rack 02, PDU A/B      │    │ Rack 03, PDU A/B      │
             │ IP: 10.100.1.11       │     │ IP: 10.100.1.12       │    │ IP: 10.100.1.13       │
             └───────────────────────┘     └───────────────────────┘    └───────────────────────┘
```

### Key Hardware Requirements:
1. **Host Isolation**: Three separate physical 1U server chassis located across distinct racks with independent power circuits.
2. **Dedicated Interconnect**: Out-of-band low-latency heartbeat network (VLAN 100) running with $< 50\text{ \mu s}$ one-way latency.
3. **Hardware Storage Isolation**: Local Enterprise NVMe U.2 storage with power-loss protection (PLP) per host. No shared SAN/NFS on write WAL paths.
4. **Time Reference**: Sub-microsecond time synchronization via PTP (IEEE 1588v2) disciplined by a dedicated grandmaster clock.

---

## 5. Current Phase 11 Staging Topology (Mode B Local Multi-Process)

Because external bare-metal hosts and bridged VM networks are not provisioned on this workstation, Phase 11 deploys an advanced **3-Node Networked Cluster** using separate OS processes listening on distinct network ports:

```text
Cluster ID: mdrap-phase11-cluster
Host: DESKTOP-MDRAP (Single Physical Host)
Operating System: Windows 11 Enterprise x86_64
Node 01: PID [Isolated Process], Ports HTTP 8111, Ingress 9111, DB data/cluster_p11/node_01.db
Node 02: PID [Isolated Process], Ports HTTP 8112, Ingress 9112, DB data/cluster_p11/node_02.db
Node 03: PID [Isolated Process], Ports HTTP 8113, Ingress 9113, DB data/cluster_p11/node_03.db
Consensus: Quorum-based lease with monotonic epoch fencing (majority = 2/3)
```

### Environmental Verdict & Gate Implication:
- **Acceptance Gate G2 (Independent-Host Topology)**: Must be classified as **BLOCKED / ENVIRONMENT-LIMITED**.
- **Software Distributed Guarantees**: Can be rigorously certified across process, socket, and storage failure boundaries, but physical isolation cannot be claimed until Tier 4 bare-metal hardware is provisioned.
