# MDRAP Phase 9 — Residual Risk Register

## 1. Risk Governance Overview
This document records the **residual operational, technical, and environmental risks** identified during the Phase 9 Controlled UAT Deployment.

Every residual risk is cataloged with its impact severity, likelihood, operational manifestation, existing mitigations, and required pre-production exit criteria.

---

## 2. Residual Risk Matrix

| Risk ID | Risk Category | Risk Summary | Severity | Likelihood | Current Mitigation | Resolution Gate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **RISK-01** | Environment | Mode A Single-Host Isolation vs Multi-Host Partitioning | HIGH | HIGH | Quorum-based lease coordination with monotonic epoch fencing verified in multi-process UAT. | Mode B Physical Multi-Host Cluster |
| **RISK-02** | Connectivity | Absence of Live Exchange Multicast Cross-Connects | HIGH | HIGH | Fail-closed authorization gate; high-fidelity historical ITCH/SBE replay verified. | Mode C Live Cross-Connect Procurement |
| **RISK-03** | Hardware | Kernel-Bypass NICs & PTP Clocks Physically Absent | MEDIUM | HIGH | Emulated software sockets and SHM ring buffer verified; Linux Onload/DPDK lab plan specified. | Mode D Hardware Colocation Lab |
| **RISK-04** | Concurrency | CPython GIL Scaling Ceiling Beyond 250 Concurrent Readers | MEDIUM | MEDIUM | AsyncFanoutManager decouples publisher; per-client queues; multi-process sharding runbook created. | Staging Scale Test (Multi-Process) |
| **RISK-05** | Storage | SQLite Write Concurrency Ceiling Under Extreme Burst (>500k eps) | LOW | MEDIUM | IngestLog segmented write-ahead log buffers synchronous disk writes; batch commits via executemany. | Production IngestLog Tier |

---

## 3. Detailed Risk Analysis & Action Plans

### RISK-01: Multi-Process Emulation vs Real Network Partitioning
- **Analysis**: In Mode A, processes communicate over loopback TCP (`127.0.0.1`) and filesystem locks. While process crashes, lease timeouts, and zombie writer behavior were validated, physical switch reboots, asymmetric routing, and packet drops across physical network cards cannot occur in loopback.
- **Action Plan**: Deploy MDRAP across 3 independent physical Linux servers in a staging VLAN (Mode B) and execute automated chaos netem packet loss drills before live production deployment.

### RISK-02: Live Exchange Protocol Nuances
- **Analysis**: Replay feeds simulate captured binary streams, but live exchange feeds exhibit unannounced micro-bursts, session resets, heartbeat intervals, and out-of-band market state changes (e.g. trading halts).
- **Action Plan**: Enforce a mandatory 2-week passive shadow-feed run (Mode C) with divergence telemetry comparing MDRAP output against incumbent exchange feed handlers before routing orders.

### RISK-03: OS Jitter & Timer Precision on Windows
- **Analysis**: Windows 11 Enterprise thread scheduler exhibits higher context switch latency ($1\text{--}15\text{ \mu s}$) and clock jitter compared to an isolcpus real-time Linux kernel ($< 500\text{ ns}$).
- **Action Plan**: Final production deployment must be hosted on Rocky Linux 9 / RHEL 9 with dedicated CPU core pinning and hugepages configured according to `hardware_validation_gate.md`.

### RISK-04: GIL Saturation Under High Reader Density
- **Analysis**: If 250+ downstream consumers connect to a single Python process, GIL contention during queue draining degrades egress throughput.
- **Action Plan**: Utilize the multi-process fanout architecture where client connections are sharded across separate child processes communicating with the core engine via POSIX/Windows shared memory.

---

## 4. Risk Sign-Off
All residual risks are thoroughly documented, bounded by architectural mitigations, and governed by explicit deployment gates. None represent an unmitigated blocker for Controlled UAT completion in Mode A.
