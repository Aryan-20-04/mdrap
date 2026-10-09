# MDRAP Phase 8 — Distributed Failure Model & Fault Analysis

## 1. Failure Modes & System Behavior

| Failure Scenario | Impact | System Response & Mitigation | Safety Invariant Preserved |
|---|---|---|---|
| **Primary Crash** | Primary terminates immediately. | Heartbeats stop; lease expires after 500ms; secondary elects itself at Epoch + 1. | **INV-08** (Single Writer Ownership) |
| **Network Partition (Minority)** | Isolated leader cannot reach quorum. | Lease renewal fails (`QuorumLossError`); leader abdicates; cannot write. | **INV-13** (Split-Brain Prevention) |
| **Network Partition (Majority)** | Majority elects new leader. | New leader issues new Epoch; WAL accepts writes from new leader only. | **INV-02** (Monotonic Sequences) |
| **Partition Heals (Split Leader Reconnection)** | Old primary attempts to resume writing. | Authoritative WAL detects $\text{Epoch}_{\text{old}} < \text{Epoch}_{\text{active}}$ and rejects write via `FencingTokenError`. | **INV-13** (Stale Leader Fencing) |
| **Slow Secondary Lag** | Secondary lags behind primary WAL. | Primary does not block on secondary replication; secondary catches up via streaming replay. | **INV-10** (No Head-of-Line Blocking) |

## 2. Zero Split-Brain Proof
Given a cluster of size $N=3$ with quorum threshold $Q=2$:
- A partition divides nodes into subsets $S_1$ and $S_2$.
- Either $|S_1| \ge 2$ or $|S_2| \ge 2$, but not both.
- The subset with $|S| < 2$ cannot obtain 2 votes and therefore cannot elect a leader or renew a lease.
- Conflicting simultaneous writers are impossible under this protocol.
