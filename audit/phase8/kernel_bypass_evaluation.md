# MDRAP Phase 8 — Kernel-Bypass Feasibility & Technology Evaluation

## 1. Executive Summary & Objective
Kernel bypass aims to eliminate operating system kernel overhead (interrupts, context switches, network stack buffer copies) by delivering incoming Ethernet frames directly into userspace application memory. This evaluation assesses the viability of candidate kernel-bypass technologies for MDRAP.

## 2. Kernel-Bypass Technology Comparison

| Technology | Supported OS | Driver / Hardware Dependency | Latency Profile | Engineering Complexity | Viability for MDRAP |
|---|---|---|---|---|---|
| **Solarflare Onload (`libonload`)** | Linux Enterprise (RHEL, Ubuntu) | Proprietary Solarflare NICs (XtremeScale) | $0.8 - 1.5\text{ \mu s}$ | Low (Drop-in `LD_PRELOAD` POSIX sockets) | **High (Target Linux Prod)** |
| **DPDK (Data Plane Dev Kit)** | Linux / Windows | Broad PCIe NIC support (Intel, Mellanox) | $0.5 - 1.2\text{ \mu s}$ | Extreme (Poll-mode drivers, dedicated cores) | **Medium (Dedicated Ingress)** |
| **Linux AF_XDP (eBPF)** | Linux 5.4+ kernel | Any driver supporting XDP (Intel, Broadcom) | $1.2 - 2.5\text{ \mu s}$ | Moderate (Zero-copy UMEM ring buffers) | **High (Cloud/Hybrid Linux)** |
| **Windows Registered I/O (RIO)** | Windows Server / Win 10+ | Standard Windows NICs with RIO support | $2.0 - 4.5\text{ \mu s}$ | High (Winsock extension API) | **Moderate (Windows On-Prem)** |
| **Standard OS Sockets (Baseline)** | Windows / Linux / macOS | Universal | $10.2 - 14.1\text{ \mu s}$ | Zero (Standard stdlib `socket`) | **Current Verified Baseline** |

## 3. Measured Impact vs Baseline
From empirical benchmark `audit/phase8/hardware_benchmark_results.json`:
- **Current Standard OS Stack**: $p50 = 10.2\text{ \mu s}$ (UDP), $13.5\text{ \mu s}$ (TCP); $p99 = 26.5 - 55.3\text{ \mu s}$.
- **Expected Solarflare Onload Gain**: Reduces wire-to-userspace transit from ~12 $\mu$s to ~1.2 $\mu$s (10x improvement), with $p99$ tail drop from 55 $\mu$s to < 3 $\mu$s by eliminating OS scheduling jitter.

## 4. Architectural Recommendation
1. For development, local testing, and multi-platform CI: maintain the standard Python/C socket stack with zero external dependencies.
2. For production co-location deployments with Solarflare NICs: enable Solarflare Onload via `onload <command>` without altering application code.
3. For dedicated ultra-low-latency ingress gateways: implement an optional C-level AF_XDP / DPDK raw ring buffer plugin interfacing with MDRAP's native `fastpath.c` SBE decoder.
