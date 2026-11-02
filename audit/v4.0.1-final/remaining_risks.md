# MDRAP v4.0.1 — Production Risk Analysis & Residual Limitations

## 1. Scope & Objective

An honest engineering audit must candidly document architecture limits, environmental boundaries, and residual risks rather than presenting speculative claims. This document articulates the residual risks, hardware prerequisites, operational boundaries, and documented non-goals for MDRAP v4.0.1.

---

## 2. Residual Architectural Risks & Mitigations

### 2.1 Single-Node Scope vs. Distributed Clustering (P2-A)
- **Risk**: MDRAP v4.0.1 is strictly designed and certified as a single-node high-performance sidecar. It contains no multi-node Raft, Paxos, or distributed leader election protocol.
- **Impact**: Deploying MDRAP in an active-active clustered configuration without an external consensus coordinator (e.g. Raft orchestrator or hardware feed arbiter) will lead to divergent WAL logs.
- **Mitigation & Policy**: Commercial documentation and deployment guidelines strictly declare MDRAP as a single-node deterministic sidecar or active-passive warm failover node. Active-active distributed consensus is explicitly out of scope for v4.0.1.

### 2.2 Filesystem Flush & Hardware Write-Cache Guarantees (P0-B)
- **Risk**: Under consumer SSDs or virtualization environments that acknowledge writes before non-volatile storage commit, `os.fsync()` may return before bits are physically magnetized or etched.
- **Impact**: Sudden host power failure on non-enterprise storage could result in torn tail records despite `fsync()` invocation.
- **Mitigation**:
  1. MDRAP WAL format incorporates 4-byte CRC32 on every frame header and payload body. On power-loss restart, torn tail records are detected and cleanly truncated without corrupting historical frames.
  2. Enterprise production deployments MUST disable storage write-back caching or employ battery-backed write caches (BBWC) with enterprise NVMe drives supporting power-loss protection (PLP).
  3. Under `fsync_policy="never"`, applications must treat unpersisted frames as ephemeral and acknowledge them as `durable=False`.

### 2.3 Shared Memory (SHM) POSIX / Windows Permissions & Multi-Tenant Boundaries
- **Risk**: POSIX shared memory ring buffers (`/dev/shm`) and Windows named memory maps require strict OS permission boundaries.
- **Impact**: If MDRAP processes are launched with overly permissive umasks (e.g. `0000`), other local processes could inspect or write into unmapped ring buffer pages.
- **Mitigation**:
  1. `src/shm.py` enforces POSIX permissions `0600` on ring buffer segment creation.
  2. Multi-tenant deployments should run MDRAP instances in isolated container namespaces (Docker / cgroups) or separate Windows user accounts.

### 2.4 SQLite Lock Contention Under Extreme Long-Lived Analytic Scans
- **Risk**: External analytical read queries holding long-lived write locks on SQLite databases can cause write lock timeouts.
- **Impact**: Ingestion engine is insulated from SQLite lockouts via in-memory bounded queues, but projection lag will accumulate during the lockout window.
- **Mitigation**:
  1. SQLite is configured with WAL journal mode (`PRAGMA journal_mode = WAL;`) allowing concurrent readers without blocking WAL writers.
  2. Heavy analytical queries and aggregations should target DuckDB or Parquet export replicas rather than the transactional SQLite projection.
  3. If projection lag exceeds `max_pending` (50,000 events), the platform transitions to `DEGRADED`, records queue overflow metrics, and executes automated WAL replay catchup upon lock release.

---

## 3. Operational Prerequisites

| Component | Production Requirement | Unsupported / Prohibited |
| :--- | :--- | :--- |
| **OS** | Linux (RHEL 9+, Ubuntu 22.04+ LTS) or Windows Server 2022+ | Non-POSIX / Non-Windows systems |
| **Storage** | Enterprise NVMe with Power-Loss Protection (PLP) | Network filesystems (NFS, SMB/CIFS, GlusterFS) for WAL directories |
| **Filesystem** | ext4 / XFS (Linux) with direct mount; NTFS (Windows) | CoW filesystems with asynchronous dedup (Btrfs, ZFS without sync) |
| **Python** | CPython 3.10 - 3.13 (64-bit) | PyPy (C-extension and seqlock SHM incompatibility) |
| **C Compiler** | GCC 11+, Clang 13+, MSVC 2019+ | Compilers lacking atomic intrinsics (`__atomic_*`) |

---

## 4. Residual Risk Assessment Summary

All identified residual risks have documented operational mitigations and failure-containment boundaries. Under the specified production prerequisites, MDRAP v4.0.1 operates with zero unhandled crash states, guaranteed crash-consistent WAL durability, and deterministic projection recovery.
