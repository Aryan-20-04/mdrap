# MDRAP Platform Status & Verification Assessment

This document provides an honest, auditable record of the verification status of MDRAP as of Phase 11 (`v3.1.0`). It clearly delineates between verified platform tiers, explicitly gated deployment modes, and known architectural boundaries.

---

## 1. Executive Summary

| Operating Mode | Scope | Verification Status | Operational Clearance |
| --- | --- | --- | --- |
| **Mode A** | Single-Host Process Isolation & Native IPC | **VERIFIED** | Sandbox & Single-Node Production Approved |
| **Mode B** | Multi-Host Distributed Clustering & Failover | **NOT SUPPORTED** | Strictly Single-Node Scope (Non-Goal) |
| **Mode C** | Live Venue Connectivity & Production Exchange Feeds | **GATED** | Requires Direct Licensing & Compliance Sign-Off |
| **Mode D** | Physical Kernel-Bypass & Hardware Acceleration | **GATED** | Requires Dedicated FPGA / NIC Testbenches |

---

## 2. What Is Verified

### 2.1 Test Suite & Mathematical Parity
- **1,275 Automated Tests**: Clean baseline test execution on Windows AMD64 (`suite_baseline_20261015.log`; 60 slow/network tests deselected by default).
- **Dual-Tier Parity Guarantee**: Native C hot-path kernel (`src/fastpath.c`, `src/mdrap_core.c`) and pure Python fallback produce bit-for-bit identical outputs for all financial quality checks and deduplication states on supported event types.
- **Strict Boolean Rejection**: Strict validation preventing Python `bool` instances (which subclass `int`) from passing numeric schema checks for prices, sizes, timestamps, or sequence numbers.

### 2.2 Storage & Durability Engine (IngestLog)
- **WAL-First IngestLog Append**: Every admitted market event is durably committed to an IEEE CRC-32 checksummed WAL segment with synchronous fsync before an acknowledgement is returned.
- **100% Acknowledged-Write Recovery**: Validated across abrupt power loss and SIGKILL chaos drills with zero data loss on acknowledged records.
- **Decoupled Monotonic Run Identity**: Run-scoped `{run_id}-{n}` identifiers eliminate SQLite primary key collisions across service restarts.

### 2.3 Single-Node Shared Memory & Local Streaming (IPC)
- **Local SPSC Shared Memory Ring Buffer**: 128-byte cache-line aligned seqlock slots for sub-microsecond IPC between co-located processes on the same host.
- **Local Loopback TCP Streaming**: Asynchronous socket distribution with bounded per-client queues protecting the engine from slow consumers.

### 2.4 Cryptographic Lineage & Auditing
- **SHA-256 Merkle Chain**: Every canonical event and quarantine decision forms an immutable, cryptographically verifiable hash chain.
- **HMAC Checkpoints**: Local HMAC-SHA256 signing of `(entry_count, head_hash)` checkpoints guarantees prefix integrity against local database tampering.

---

## 3. What Is NOT Verified / Explicitly Gated

### 3.1 Mode C: Live Exchange Feed Connectivity
- **Status**: **GATED**
- **Reasoning**: MDRAP is software infrastructure and does not resell, broker, or provide exchange market data. Connecting to live production venues (e.g., NASDAQ TotalView-ITCH, CME MDP 3.0, ICE, OPRA, Cboe) requires customer-secured commercial licenses, exchange redistribution agreements, and network provisioning.
- **Condition for Activation**: Customer credentials, network access, and compliance sign-off.

### 3.2 Mode D: Physical Kernel-Bypass Hardware Validation
- **Status**: **GATED**
- **Reasoning**: Kernel-bypass validation requires dedicated physical hardware testbenches (Solarflare Onload, Mellanox VMA, or PCIe FPGA capture cards) with PTP hardware timestamping. Virtualized network interfaces and standard OS sockets cannot validate sub-microsecond wire-to-memory hardware latencies.
- **Condition for Activation**: Deployment on dedicated bare-metal servers equipped with supported network accelerators.

### 3.3 Public PyPI Package Availability
- **Status**: **NOT PUBLISHED**
- **Reasoning**: MDRAP is not published on public PyPI. Installation is supported only from local Git checkouts or internal private package repositories.

### 3.4 Multi-Host Distributed Consensus & Clustering
- **Status**: **NOT SUPPORTED / ARCHITECTURAL NON-GOAL**
- **Reasoning**: MDRAP is strictly scoped as a single-node embedded validation and capture engine. It does not provide distributed Raft/Paxos quorum election, multi-host network partitioning recovery, or cross-datacenter state replication. See [Scope & Guarantees Specification](product/scope-and-guarantees.md) for supported topologies.

---

## 4. Known Architectural Boundaries & Open Issues

### 4.1 Adapter Lifecycle Supervision
> [!NOTE]
> **There is no supervisor that owns adapter lifecycle.**
> The REST API endpoint `POST /v1/feeds` records feed configuration metadata and reports `REGISTERED_NOT_RUNNING`. The API server does not start, supervise, or reconnect background socket feeds. Inbound market data must be fed into the platform via `POST /v1/ingest`, through the standalone gateway (`mdrap gateway`), or by directly embedding the `mdrap.Engine` Python API.

### 4.2 WebSocket Broadcaster Scope
- The API WebSocket endpoint (`/v1/events/stream`) broadcasts canonical events that have been submitted to `POST /v1/ingest`. It does not automatically stream from an unmanaged external exchange socket.

### 4.3 Broad Exception Handling in Non-Critical Paths
- While core hot paths and normalization kernels employ strict, typed error boundaries, some legacy peripheral paths (such as background watchdog ping loops and diagnostic telemetry) retain broader exception handling blocks.

### 4.4 Off-Box Checkpoint Replication
- Audit log HMAC checkpoints are currently verified against local state on disk. Automated off-box export to object storage (e.g., AWS S3 with Object Lock or GCP Cloud Storage) must be configured externally or executed via scheduled archival scripts (`mdrap audit export`).
