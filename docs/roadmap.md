# MDRAP Platform Roadmap & Aspirational Capabilities

This document tracks planned architecture enhancements, advanced hardware integrations, and distributed consensus features that are currently categorized as **Roadmap**, **Gated**, or **Experimental**.

In accordance with Design Principle 2 (*Measure before claiming*), features listed here are not part of the standard production single-node baseline until fully implemented, benchmarked, and verified by reproducible tests.

---

## 1. High Availability & Distributed Consensus (Mode B)

### Current Implementation Status: Active-Passive WAL Streaming
MDRAP currently implements point-to-point TCP streaming Write-Ahead Log replication (`src/mdrap/journal_replicator.py`) with CRC-32 segment verification, offline spooling, and failover state tracking (`PRIMARY`, `STANDBY`, `SYNCING`, `FAILED`).

### Planned Aspirational Milestone:
- **Multi-Host Raft / Paxos Consensus**: Automatic distributed leader election, quorum commits, and split-brain fencing without manual operator promotion.
- **Cross-Datacenter Low-Latency Mesh**: WAN-optimized differential state synchronization between primary financial hubs (e.g. Equinix NY4, LD4, and TY3).

---

## 2. Hardware Offload & Kernel Bypass (Mode D)

### Current Implementation Status: Software Simulation & Linux Socket Hooks
- `src/mdrap/af_xdp.py`: Software emulation of virtual UMEM chunk pools and batch ring buffer polling.
- `src/mdrap/clock.py`: High-precision Linux `SO_TIMESTAMPING` cmsg hardware timestamp dissector and drift simulation model (`PTPClock`).

### Planned Aspirational Milestone:
- **Bare-Metal AF_XDP Production Deployment**: Zero-copy packet reception directly from 25/100 GbE NIC rings to userspace memory on enterprise Linux kernels.
- **FPGA PCIe DMA Offload**: Offloading SBE message framing and price sanity filtering directly onto FPGA logic (e.g. AMD Xilinx Alveo U50), achieving sub-20ns ingress validation.
- **Dedicated PTP Hardware Clocks**: Sub-microsecond time synchronization locked to IEEE 1588 Grandmaster PTP GPS clocks.

---

## 3. Cryptographic Anchoring & External Notarization

### Current Implementation Status: Internal SHA-256 Merkle Chains
MDRAP maintains an unbroken internal cryptographic hash chain (`audit_log` table) and signs periodic checkpoints with HMAC-SHA256, enabling offline mathematical proof verification (`verify_audit_integrity`).

### Planned Aspirational Milestone:
- **RFC 3161 Timestamping Authority (TSA)**: Automated periodic submission of Merkle root digests to certified public RFC 3161 timestamping servers.
- **Public Blockchain Anchoring**: Automated anchoring of daily end-of-day audit roots to public distributed ledgers for external third-party tamper proofs.

---

## 4. Feed Supervision & Autonomous Recovery

### Current Implementation Status: Standalone Adapters & Gateway Ingestion
Exchange feed adapters (`src/mdrap/adapters/`, `src/mdrap/ws_feed.py`) run as independent client processes or dedicated CLI commands. The API server stores feed registration metadata (`REGISTERED_NOT_RUNNING`).

### Planned Aspirational Milestone:
- **Integrated Adapter Process Supervisor**: Built-in process manager inside the API service to spawn, monitor, restart, and load-balance live exchange WebSocket connections automatically.
- **Dynamic Book Resynchronization**: Automated snapshot reconciliation with local order book deltas when sequence gaps exceed recoverable thresholds.
