# Phase 5 Deployment Architecture Specification

**Platform**: Market Data Reliability & Acceleration Platform (MDRAP v3.0.0)  
**Profile**: Profile A (Single-Node High Throughput) & Profile B (Active-Passive HA)  
**Date**: 2026-10-09  

---

## 1. Production Deployment Topology

MDRAP deploys as a standalone system service or containerized daemon sitting on the network perimeter before downstream trading consumers.

```text
[External Feeds: Multicast / WebSocket / Direct Cross-Connect]
                          │
                          ▼
            ┌───────────────────────────┐
            │   MDRAP Ingress Daemon    │
            │                           │
            │   - Replay / Direct Feed  │
            │   - Normalization         │
            │   - Quality Rule Engine   │
            └─────────────┬─────────────┘
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
 ┌──────────────────────┐    ┌──────────────────────┐
 │    IngestLog WAL     │    │   SBE Binary Wire    │
 │ (/var/lib/mdrap/wal) │    │  (IPC Ring / Socket) │
 └──────────────────────┘    └──────────┬───────────┘
                                        │
                                        ▼
                             [Independent Consumers]
                             (C++17 / Python / Java)
```

---

## 2. Directory Layout & Permissions

| Path | Purpose | Permissions | Owner |
| :--- | :--- | :--- | :--- |
| `/etc/mdrap/mdrap.toml` | Operational configuration | `0644` | `root:mdrap` |
| `/var/lib/mdrap/wal/` | IngestLog segmented WAL (`.log`, `.lock`) | `0700` | `mdrap:mdrap` |
| `/var/lib/mdrap/data/` | Durable SQLite metering database | `0700` | `mdrap:mdrap` |
| `/var/log/mdrap/` | Structured application logs | `0750` | `mdrap:mdrap` |
| `/dev/shm/mdrap_*` | Shared memory IPC segments (Linux) | `0600` | `mdrap:mdrap` |

---

## 3. High Availability Deployment (Profile B)

- **Node Allocation**: Primary node (`node-1`) and Standby node (`node-2`) configured on independent rack power domains.
- **Heartbeat Channel**: Dedicated point-to-point UDP or loopback socket on port `9876`.
- **Fencing Model**: Epoch increment with monotonic fencing token passed in all commit operations. Demoted or isolated primaries are locked out with `StaleEpochError`.
