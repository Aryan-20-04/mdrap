# MDRAP Platform Roadmap & Aspirational Capabilities (v5.3+)

This document tracks completed architectural milestones, ongoing platform initiatives, and planned capabilities categorized as **Roadmap**, **Gated**, or **Experimental**.

In accordance with Design Principle 2 (*Measure before claiming*), features listed as roadmap or experimental are not part of the standard production single-node baseline until fully implemented, benchmarked, and verified by reproducible tests.

---

## 1. Summary of Completed Milestones (v5.0 – v5.3)

| Release | Primary Focus | Key Completed Capabilities |
| :--- | :--- | :--- |
| **v5.0** | High-Throughput Kernel Optimization | Decoupled userspace buffering, in-memory segment tracking, eliminated redundant JSON in hot paths, native C hot path. |
| **v5.1** | Institutional Systems Ingress & Transport | SBE binary decoders (CME MDP 3.0, Eurex T7, ITCH), SHM v3 SPMC seqlock distribution, hardware-accelerated CRC32C. |
| **v5.1.1** | Live Venue Ingestion & Matching Engine | 5 Live exchange feeds (Binance, Coinbase, Kraken, OKX, Bybit), price-time priority discrete-event matching engine, Avellaneda-Stoikov HFT MM, 14 pre-trade risk gates. |
| **v5.2** | Quantitative Validation & Forensics | Identified immediate-touch fill bias, unhedged basis arbitrage risks, and fastpath FFI coupling. Established 10-boundary benchmark suite. |
| **v5.3** | Engineering Remediation & Certification | Genuine pure-Python fallback isolation (170.1k EPS vs 510.8k EPS C fastpath), linear inventory skew MM decoupling, denomination-aware FX basis valuation, matching engine kill-switches and duplicate order protection, Bailey-López de Prado PSR/DSR institutional risk metrics. |

---

## 2. High Availability & Distributed Consensus (Mode B — Future Roadmap)

### Current Implementation Status: Active-Passive WAL Streaming
MDRAP currently implements point-to-point TCP streaming Write-Ahead Log replication (`src/mdrap/journal_replicator.py`) with CRC-32 segment verification, offline spooling, and failover state tracking (`PRIMARY`, `STANDBY`, `SYNCING`, `FAILED`).

### Planned Aspirational Milestone (v5.4+):
- **External Coordinator Lease Acquisition**: Delegated active-passive leadership leases using etcd, Consul, or ZooKeeper acquire-lease followers, ensuring zero multi-master split-brain.
- **Cross-Datacenter Low-Latency Mesh**: WAN-optimized differential state synchronization between primary financial colocation sites (e.g. Equinix NY4, LD4, and TY3).

---

## 3. Hardware Offload & Kernel Bypass (Mode D — Blueprint / Experimental)

### Current Implementation Status: Socket Hooks & Architectural Blueprints
- `src/mdrap/af_xdp.py`: Software emulation of virtual UMEM chunk pools and batch ring buffer polling.
- `src/mdrap/clock.py`: High-precision Linux `SO_TIMESTAMPING` cmsg hardware timestamp dissector and drift simulation model (`PTPClock`).

### Planned Aspirational Milestone:
- **Bare-Metal AF_XDP Driver Integration**: Zero-copy packet reception directly from 25/100 GbE NIC rings (e.g. Intel E810 / Mellanox ConnectX-6) to userspace memory on enterprise Linux kernels.
- **Dedicated PTP Hardware Clocks**: Sub-microsecond time synchronization locked to IEEE 1588 Grandmaster PTP GPS clocks.
- **FPGA PCIe DMA Offload**: Offloading SBE message framing and price sanity filtering directly onto FPGA logic (e.g. AMD Xilinx Alveo U50), achieving sub-20ns ingress validation.

---

## 4. Cryptographic Anchoring & External Notarization

### Current Implementation Status: Internal SHA-256 Merkle Chains & Notary Stubs
MDRAP maintains an unbroken internal cryptographic hash chain (`audit_log` table) and signs periodic checkpoints with HMAC-SHA256, enabling offline mathematical proof verification (`verify_audit_integrity`). Third-party timestamp certificates are supported in `mdrap-audit`.

### Planned Aspirational Milestone:
- **RFC 3161 Timestamping Authority (TSA)**: Automated periodic submission of Merkle root digests to certified public RFC 3161 timestamping servers.
- **Public Ledger Anchoring**: Automated anchoring of daily end-of-day audit roots to public distributed ledgers for external third-party tamper proofs.

---

## 5. Feed Supervision & Autonomous Recovery

### Current Implementation Status: Standalone Adapters & Live Feeds
Exchange feed adapters (`src/mdrap/live.py`, `src/mdrap/adapters/`, `src/mdrap/ws_feed.py`) run as independent client processes or dedicated CLI commands. The API server stores feed registration metadata (`REGISTERED_NOT_RUNNING`).

### Planned Aspirational Milestone:
- **Integrated Adapter Process Supervisor**: Built-in process manager inside the API service to spawn, monitor, restart, and load-balance live exchange WebSocket connections automatically.
- **Dynamic Book Resynchronization**: Automated snapshot reconciliation with local order book deltas when sequence gaps exceed recoverable thresholds.
