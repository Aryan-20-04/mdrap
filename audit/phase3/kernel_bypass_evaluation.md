# MDRAP Phase 3 — Kernel-Bypass Ingress Evaluation

**Document Identifier**: `MDRAP-BYPASS-P3-001`  
**Date**: October 9, 2026  
**Status**: FORMAL EVALUATION & GATE DECISION  

---

## 1. Executive Summary & Gate Decision

**Decision**: **CONVENTIONAL SOCKETS + SHARED MEMORY IPC REMAINS THE PRODUCTION-SUPPORTED BASELINE. KERNEL-BYPASS (AF_XDP) IS DESIGNATED EXPERIMENTAL / LINUX-ONLY.**

### Rationale:
1. **Measured Bottleneck Analysis**: In Phase 0 and Phase 2 benchmarks, MDRAP's end-to-end latency bottleneck is dominated by Python event object instantiation and SQLite transaction synchronization, not kernel network stack syscalls (`recvmsg`).
2. **Platform & Hardware Constraints**: MDRAP operates across Windows and Linux. AF_XDP requires Linux kernel 5.4+ with specific network driver support (e.g. `i40e`, `mlx5`, `ixgbe`) and `CAP_NET_ADMIN` privileges. DPDK requires dedicated PCIe NIC assignment via VFIO/UIO, 1GB hugepages, and dedicated 100% busy-polling CPU cores.
3. **Operational Complexity**: DPDK completely removes the NIC from kernel management, disabling standard tooling (`tcpdump`, `iptables`, `ip route`) and complicating container orchestration.
4. **Zero-Lock Shared Memory Parity**: For inter-process communication on the local host, MDRAP's native seqlock shared-memory ring buffer (`src/mdrap/shm.py`, `src/fastpath.c`) already delivers sub-microsecond IPC latency (p50 ~ 1.4µs) without kernel-bypass drivers.

---

## 2. Comparative Technology Matrix

| Dimension | Conventional Sockets (`epoll`/`IOCP`) | Linux AF_XDP (eBPF XDP) | Intel / Linux DPDK |
|---|---|---|---|
| **Typical Ingress p50 Latency** | 8.0 – 15.0 µs | 2.5 – 4.0 µs | 1.0 – 2.0 µs |
| **Typical Ingress p99 Latency** | 25.0 – 45.0 µs | 6.0 – 10.0 µs | 2.5 – 4.5 µs |
| **Max Throughput** | 1.5M – 2.5M pps / core | 8.0M – 12.0M pps / core | 14.0M – 20.0M pps / core |
| **CPU Utilization** | Event-driven (sleep on idle) | Configurable polling/interrupt | 100% spinlock busy-wait |
| **Hardware Dependency** | Standard NIC, any OS | Linux 5.4+, supported NIC driver | Specific PCIe NIC (Intel/Mellanox) |
| **Privileges Required** | Standard unprivileged user | `CAP_NET_ADMIN`, `bpf()` access | Root / `CAP_SYS_RAWIO`, hugepages |
| **Container / K8s Compatibility** | Excellent (standard veth) | Good (requires CNI plugin) | Complex (SR-IOV device plugin) |
| **Windows Compatibility** | Full (`WinSock2` / IOCP) | None | None |
| **Maintenance Burden** | Low (stdlib / POSIX) | Medium (kernel/eBPF dependency) | High (C ABI, custom drivers) |

---

## 3. Production Support Policy

- **Production Supported**: Conventional TCP/UDP Sockets with batch receive (`gateway_tcp.py`, `ws_feed.py`) and Win32 / POSIX Shared Memory IPC (`shm.py`).
- **Deferred / Experimental**: AF_XDP driver backend for bare-metal Linux instances with Mellanox ConnectX-5/6 NICs.
