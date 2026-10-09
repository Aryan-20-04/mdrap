# MDRAP Phase 11 — Residual Risk Register & Mitigation Strategy

## 1. Executive Summary
This document catalogs the active **Residual Risks** associated with MDRAP at the conclusion of Phase 11. Each risk is ranked by severity, probability, architectural impact, and institutional mitigation strategy.

---

## 2. Risk Matrix Summary

| Risk ID | Category | Title | Inherent Risk | Residual Risk | Status | Action Required |
|:---:|:---|:---|:---:|:---:|:---:|:---|
| **RSK-01** | Infrastructure | Single Physical Host Failure Domain | **CRITICAL** | **HIGH** | Open | Provision 3 bare-metal 1U servers in separate racks |
| **RSK-02** | Consistency | Clock Skew on Unsynchronized Hosts | **HIGH** | **MEDIUM** | Open | Implement PTP (IEEE 1588v2) hardware clock discipline |
| **RSK-03** | Persistence | Raw Direct SQLite Access Bypassing Fencing | **MEDIUM** | **LOW** | Controlled | Enforce process sandbox preventing direct DB access |
| **RSK-04** | Protocol | Binary Frame Format Lacks Embedded Epoch | **MEDIUM** | **LOW** | Accepted | Epoch enforced at IngestLog writer boundary |
| **RSK-05** | Network | Loopback Transport Masks Network Jitter | **HIGH** | **MEDIUM** | Open | Execute soak testing over 25GbE physical switch fabric |
| **RSK-06** | Compliance | Unprovisioned Exchange Redistribution License | **CRITICAL** | **CRITICAL** | Gated | Execute commercial agreements before Mode C activation |

---

## 3. Detailed Risk Analysis & Mitigation Plans

### RSK-01: Single Physical Host Failure Domain
- **Description**: Staging validation was executed on a single Windows 11 Enterprise workstation (`DESKTOP-MDRAP`). All 3 cluster nodes run on the same physical power supply, CPU, RAM bus, and motherboard.
- **Impact**: A host hardware power failure or kernel panic halts all nodes simultaneously, rendering quorum failover impossible.
- **Mitigation Strategy**: Restrict Phase 11 verdict to Mode B staging. Require deployment across three physically independent servers with redundant A/B power supplies prior to production sign-off.

### RSK-02: Clock Skew on Unsynchronized Hosts
- **Description**: Consensus lease duration ($T_{\text{lease}} = 500\text{ ms}$) assumes bounded clock drift between cluster nodes. On separate physical hardware, unsynchronized quartz crystals can drift by tens of milliseconds over time.
- **Impact**: Standby node could prematurely elect itself while primary still believes its lease is valid, leading to ephemeral split-brain attempts (intercepted by fencing, but causing failover jitter).
- **Mitigation Strategy**: Mandate sub-microsecond PTP (IEEE 1588v2) hardware clock synchronization disciplines on production server NICs.

### RSK-03: Raw Direct SQLite Access Bypassing Fencing
- **Description**: Monotonic epoch fencing is enforced by `FencedWALWriter` in the Python application runtime. SQLite itself does not possess native epoch gate locks.
- **Impact**: If a rogue or misconfigured process connects directly to `node_XX.db` via standard SQLite drivers, writes would not be intercepted by `FencedWALWriter`.
- **Mitigation Strategy**: Restrict OS file permissions on `node_XX.db` to a dedicated `mdrap_daemon` service account. Downstream applications must consume only through authenticated TCP/SHM interfaces.

### RSK-04: Binary Frame Format Lacks Embedded Epoch
- **Description**: `IngestLog` WAL frame headers store timestamp, event type, sequence, and payload CRC32, but do not encode the cluster `epoch` in the 28-byte binary header.
- **Impact**: If an offline WAL segment from an older epoch is manually merged into an active log directory, the reader cannot detect epoch discrepancy from frame headers alone.
- **Mitigation Strategy**: Fencing is strictly enforced on all write paths before bytes reach disk. In Phase 12, extend the binary WAL frame header format from 28 bytes to 36 bytes to include a 64-bit uint `epoch` field.

### RSK-05: Loopback Transport Masks Network Jitter & Drops
- **Description**: Sockets communicated over `127.0.0.1` and `10.21.12.27` within the Windows TCP stack, which does not simulate real packet loss, switch buffer saturation, or fiber latency.
- **Impact**: Production fan-out over physical switches may experience microburst packet drops not seen in loopback.
- **Mitigation Strategy**: Asynchronous fan-out manager includes ring-buffer backpressure queues, per-client drop counters, and automatic noisy-neighbor eviction, verified during stress tests. Full hardware switch testing remains required for Mode D.

---

## 4. Governance Verdict
Residual risks RSK-03, RSK-04, and RSK-05 are safely bounded by existing platform controls.
Residual risks **RSK-01, RSK-02, and RSK-06** represent physical and commercial boundaries that **prevent unconditioned production deployment authorization**.
MDRAP Phase 11 is formally approved as **STAGING-CERTIFIED, PRODUCTION-GATED**.
