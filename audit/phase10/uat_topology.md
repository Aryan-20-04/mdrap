# MDRAP Phase 10 — Mode B Networked Staging Topology Specification

## 1. Topology Overview & Architecture
To validate distributed failure modes, leader elections, and split-brain fencing under realistic multi-node conditions, MDRAP Phase 10 deploys a **3-node networked staging cluster**.

This 3-node topology satisfies the strict majority quorum requirement:
$$\text{Quorum Size} = \left\lfloor \frac{N}{2} \right\rfloor + 1 = \left\lfloor \frac{3}{2} \right\rfloor + 1 = 2 \text{ nodes}$$

```
                           MDRAP Mode B Staging Cluster Topology
                           =====================================
                                    ┌──────────────────┐
                                    │ Downstream Algo  │
                                    │ Client Consumers │
                                    └────────┬─────────┘
                                             │ TCP Fan-Out (810x / 910x)
                                             ▼
        ┌────────────────────────────────────────────────────────────────────────┐
        │                                                                        │
        │   ┌─────────────────────┐                 ┌─────────────────────┐      │
        │   │       NODE-01       │                 │       NODE-02       │      │
        │   │ (Primary Candidate) │◄──Quorum Sync──►│ (Sync Standby Peer) │      │
        │   │ PID: Isolated Proc  │  Heartbeat TCP  │ PID: Isolated Proc  │      │
        │   │ HTTP API: Port 8101 │                 │ HTTP API: Port 8102 │      │
        │   │ Ingress:  Port 9101 │                 │ Ingress:  Port 9102 │      │
        │   │ WAL: data/wal_01/   │                 │ WAL: data/wal_02/   │      │
        │   │ DB:  node_01.db     │                 │ DB:  node_02.db     │      │
        │   └──────────┬──────────┘                 └──────────┬──────────┘      │
        │              │                                       │                 │
        │              │                 Quorum                │                 │
        │              └──────────── Heartbeat TCP ────────────┘                 │
        │                                  ▲                                     │
        │                                  │                                     │
        │                                  ▼                                     │
        │                       ┌─────────────────────┐                          │
        │                       │       NODE-03       │                          │
        │                       │ (Quorum Peer / Tie) │                          │
        │                       │ PID: Isolated Proc  │                          │
        │                       │ HTTP API: Port 8103 │                          │
        │                       │ Ingress:  Port 9103 │                          │
        │                       │ WAL: data/wal_03/   │                          │
        │                       │ DB:  node_03.db     │                          │
        │                       └─────────────────────┘                          │
        │                                                                        │
        └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Node Specifications & Failure Domains

| Attribute | Node-01 (Primary) | Node-02 (Standby) | Node-03 (Quorum Tie-Breaker) |
| :--- | :--- | :--- | :--- |
| **Node ID** | `node-01` | `node-02` | `node-03` |
| **Initial Role** | Primary Leader (Epoch 1) | Standby Replica (Follower) | Standby Quorum Peer (Follower) |
| **Process Identity** | Independent OS Process | Independent OS Process | Independent OS Process |
| **Failure Domain** | Process Domain 1 (Host Windows) | Process Domain 2 (Host Windows) | Process Domain 3 (Host Windows) |
| **Network Interface** | TCP `127.0.0.1` / `10.21.12.27` | TCP `127.0.0.1` / `10.21.12.27` | TCP `127.0.0.1` / `10.21.12.27` |
| **HTTP API Port** | `8101` (`/health`, `/metrics`) | `8102` (`/health`, `/metrics`) | `8103` (`/health`, `/metrics`) |
| **Ingress/Control Port**| `9101` (Line Ingress) | `9102` (Line Ingress) | `9103` (Line Ingress) |
| **Consensus Lease TTL**| 500 ms (Renewal at 150 ms) | 500 ms (Heartbeat check) | 500 ms (Heartbeat check) |
| **Storage Isolation** | `data/cluster/node_01.db` | `data/cluster/node_02.db` | `data/cluster/node_03.db` |
| **WAL Log Isolation** | `data/cluster/wal_01/` | `data/cluster/wal_02/` | `data/cluster/wal_03/` |
| **Fencing Boundary** | `FencedWALWriter` (Epoch Gate) | `FencedWALWriter` (Epoch Gate) | `FencedWALWriter` (Epoch Gate) |

---

## 3. Communication, Replication & Consensus Architecture

### 3.1 Control-Plane & Quorum Heartbeats
- Nodes communicate via standard TCP sockets.
- The leader node (`node-01`) periodically broadcasts heartbeat lease renewals to peers every 150 ms.
- Each follower node verifies that the lease token contains a strictly monotonic epoch ($E \ge E_{\text{last}}$).
- Quorum is achieved when $\ge 2$ nodes agree on the active epoch leader.

### 3.2 Ingress & Persistence Write Path
- Authoritative writes are processed exclusively by the node holding the active, unexpired `EpochToken`.
- Before persisting any batch of events to the local SQLite database or IngestLog WAL, the node's `FencedWALWriter` validates:
  1. `not token.is_expired`
  2. `token.epoch >= highest_epoch_seen`
- Any stale node attempting writes after being demoted or partitioned is immediately rejected with `FencingTokenError`.

### 3.3 Fault & Partition Injection Mechanics
1. **Process Hard-Kill (`SIGKILL`)**: Abrupt termination via `process.kill()`, evaluating lease expiry and automatic standby takeover.
2. **Network Partition (Simulated Socket Cut)**: Closing inter-node communication sockets while leaving the processes running to simulate asymmetric network drops.
3. **Slow Consumer Throttling**: Injecting zero-consumption readers into the fan-out broadcast stream.

---

## 4. Hardware & Environment Realities
In strict adherence to Phase 10 Mandatory Rule §1.6:
- **Current Mode**: **Mode B (Emulated Local Multi-Process over Network Sockets)**.
- **Physical Multi-Host Separation**: Distinct physical servers across separate power and network top-of-rack switches are **GATED / ENVIRONMENT-LIMITED** in this development environment.
- **Socket Communication**: Sockets bind to authentic loopback and LAN interfaces, ensuring real OS kernel network stack traversals for all tests.
