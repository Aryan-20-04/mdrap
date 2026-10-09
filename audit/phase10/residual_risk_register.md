# MDRAP Phase 10 — Residual Risk Register

## 1. Executive Summary
This document defines the **Residual Risk Register** for MDRAP Phase 10. While Phase 10 successfully validates Mode B Networked Staging (multi-process cluster coordination over TCP sockets, failover lifecycle, and client fan-out), critical operational risks remain before production deployment can be authorized.

## 2. Risk Classification Matrix

| Risk ID | Risk Title | Category | Staging Severity | Production Severity | Mitigation / Prerequisite for Production |
|:---:|:---|:---:|:---:|:---:|:---|
| **RSK-01** | **Single-Host Physical Failure Domain** | Infrastructure | LOW | **CRITICAL** | Physical multi-host / multi-AZ deployment with independent power and networking. |
| **RSK-02** | **Absence of Live Exchange Feeds (Mode C)** | Market Data | ACCEPTABLE | **HIGH** | Execution of Mode C live shadow-feed validation with exchange licensing. |
| **RSK-03** | **Unvalidated Kernel-Bypass Hardware (Mode D)** | Network / Latency | ACCEPTABLE | **HIGH** | Deployment on certified Linux hardware with Solarflare Onload / DPDK NICs. |
| **RSK-04** | **SQLite WAL Multi-Host Storage Boundary** | Durability | LOW | **HIGH** | Strict enforcement of active-passive local WAL; no shared NFS/SMB mounts. |
| **RSK-05** | **Windows OS High-Resolution Clock Jitter** | Telemetry | LOW | **MEDIUM** | Deployment on bare-metal Linux with PTP (IEEE 1588) hardware clock sync. |

---

## 3. Detailed Risk Profiles

### RSK-01: Single-Host Physical Failure Domain
- **Description**: In Mode B testing, all 3 cluster nodes execute as separate OS processes communicating over TCP loopback / local IP on a single Windows 11 host.
- **Consequence**: A host-level hardware fault, hypervisor crash, kernel panic (BSOD), or power outage terminates all cluster nodes simultaneously, rendering quorum failover moot.
- **Go/No-Go Gate**: **BLOCKED for Production**. Mode B proves algorithm correctness, but Production requires physical hardware separation.

### RSK-02: Absence of Live Exchange Feeds (Mode C)
- **Description**: Phase 10 testing utilized high-fidelity multi-feed simulation and recorded historical data replays.
- **Consequence**: Synthetic workloads may not capture edge cases such as sudden 100x market volatility micro-bursts, exchange sequence resets, unexpected packet retransmissions, or exchange multicast gap fills.
- **Go/No-Go Gate**: **BLOCKED for Production** until Mode C operational soak is completed under licensed market data agreements.

### RSK-03: Unvalidated Kernel-Bypass Hardware (Mode D)
- **Description**: Network egress in Phase 10 was validated using OS kernel TCP network sockets (`socket.SOCK_STREAM`), reaching ~32,000 fps with ~100 µs publisher latency.
- **Consequence**: Sub-5-microsecond tick-to-trade SLAs cannot be achieved over standard OS TCP stacks without kernel-bypass technologies (Solarflare EF_VI / Onload or DPDK).
- **Go/No-Go Gate**: **BLOCKED for Ultra-Low Latency Tier** until Mode D hardware benchmarking is executed on target physical appliances.

### RSK-04: SQLite WAL Multi-Host Storage Boundary
- **Description**: SQLite Write-Ahead Logging (WAL) requires shared-memory (`-shm`) and advisory file locks (`-wal`) that fail unpredictably over networked filesystems (NFS, SMB, CIFS).
- **Consequence**: If multiple nodes attempt to mount a single shared network volume as their SQLite database path, silent database corruption will occur.
- **Mitigation**: MDRAP architecture strictly dictates local-disk storage per node. Distributed replication must operate via consensus-replicated IngestLog streams, never via shared SQLite files.

### RSK-05: Windows OS Clock Resolution & Jitter
- **Description**: Benchmark timings rely on Windows `QueryPerformanceCounter` (`time.perf_counter_ns()`).
- **Consequence**: Dynamic CPU frequency scaling (Intel SpeedStep / AMD Cool'n'Quiet) and non-real-time OS thread scheduling can introduce ±10–50 µs timing jitter.
- **Mitigation**: Production environments must utilize Linux isolcpus, taskset core pinning, and PTP hardware timestamping.

## 4. Residual Risk Assessment
None of the listed residual risks compromise the internal correctness, data integrity, or fencing safety of MDRAP. However, they represent definitive operational boundaries that prevent immediate production deployment without the required physical infrastructure.
