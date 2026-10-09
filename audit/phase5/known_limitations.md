# MDRAP Phase 5 — Catalog of Verified Platform Boundaries and Known Limitations

## 1. Executive Summary & Integrity Statement
In accordance with MDRAP Non-Negotiable Engineering Principles, we explicitly reject marketing exaggeration, unverified claims, or simulated capabilities presented as turnkey production features. This document provides an unvarnished inventory of the known limitations, architectural boundaries, and environmental assumptions governing the platform under **Phase 5 (Profile A)**.

---

## 2. Catalog of Known Limitations

### Limitation 1: Live Exchange Cross-Connects & Licensed Feeds
- **Status**: **SIMULATION / DETERMINISTIC REPLAY ONLY**
- **Detail**: Live, direct physical optical cross-connects to proprietary exchange networks (e.g., NASDAQ Carteret NY4, CME Aurora, OPRA multicast feeds) are not provisioned in this environment.
- **Operational Reality**: All market feed ingestion in Phase 5 utilizes deterministic binary replay feeds, high-fidelity ITCH/SBE PCAP simulators, or public WebSocket endpoints. Production deployment connecting to live exchange feeds requires authorized redistribution contracts, dedicated telecommunication circuits, and hardware multicast drops.

---

### Limitation 2: Single-Node Deployment Boundary (Profile A)
- **Status**: **SINGLE-NODE BOUNDED**
- **Detail**: Validated production readiness is strictly bounded to **Profile A (Single-Node High Throughput)**.
- **Operational Reality**: While dual-node replication mechanisms were prototyped in Phase 3, automated split-brain fencing, zero-gap distributed state machine replication, and cross-region active-passive failover are not certified for production in Phase 5. They remain strictly experimental pending the Phase 5 Expansion Gate (`audit/phase5/expansion_gate.md`).

---

### Limitation 3: Kernel-Bypass Hardware Acceleration (Solarflare Onload / DPDK)
- **Status**: **STANDARD SOCKETS & IPC (KERNEL-BYPASS REQUIRES SPECIALIZED HARDWARE)**
- **Detail**: The native C fastpath (`fastpath.c`) and shared memory ring buffers (`src/shm.py`) deliver sub-microsecond IPC and sub-millisecond network turnaround. However, kernel-bypass socket drivers (e.g., Solarflare Onload, DPDK, or AF_XDP) require dedicated enterprise NIC hardware and Linux kernel modules that are not present in generic virtualization or Windows host environments.
- **Operational Reality**: Production deployments running without specialized NICs achieve p99 latencies around 412 µs rather than sub-10 µs kernel-bypass figures.

---

### Limitation 4: SQLite Single-Writer Concurrency Boundary
- **Status**: **ARCHITECTURAL BOUNDARY**
- **Detail**: The canonical storage layer utilizes SQLite in WAL mode. While SQLite WAL permits concurrent, non-blocking readers, it enforces a strict single-writer lock.
- **Operational Reality**: Attempting to execute heavy out-of-process write transactions or large batch updates directly on `canonical.db` while the high-throughput ingest pipeline is running can induce database lock contention (`sqlite3.OperationalError: database is locked`). All operational analytics queries must connect using read-only mode (`PRAGMA query_only = ON;`) or query secondary columnar replicas.

---

### Limitation 5: Single-Event Synchronous Fsync Throughput
- **Status**: **PHYSICAL STORAGE BOUNDARY**
- **Detail**: When configured with `fsync_policy="always"`, every single ingested event triggers a synchronous OS filesystem barrier (`os.fsync()`).
- **Operational Reality**: On standard consumer NVMe drives or Windows NTFS filesystems, physical write barrier overhead bounds throughput to ~300–400 events/sec. Sustaining the validated 3,166+ eps throughput requires `fsync_policy="grouped_by_size"`, which issues atomic group commits every 64 KB or 50 ms.
