# MDRAP Phase 6 — Replication Architecture, Partition Fencing, and Failover Guarantees

## 1. Executive Summary & Design Invariants
In distributed and partitioned market data infrastructure, split-brain states—where two competing processes simultaneously claim ownership of the same partition—lead to unrecoverable sequence divergence, phantom executions, and corrupt audit trails.

MDRAP Phase 6 defines an explicit **Replication and Fencing Model** governed by:
1. **Single-Writer Partition Invariant**: Exactly one active writer instance is permitted per symbol partition at any point in physical time.
2. **Exclusive OS Filesystem Fencing**: Process ownership is anchored to an exclusive, non-blocking lock file (`shard.lock`) using kernel-level file descriptor locks (`flock` on POSIX, `LockFileEx` on Windows).
3. **Monotonic Epoch Fencing**: Each shard lifecycle generation increments an integer epoch (`epoch.seq`). Out-of-epoch frames arriving at downstream consumers or replicas are rejected.
4. **Asynchronous Cross-Host / Cross-Site Streaming**: For high availability, primary shards stream atomic WAL segments to secondary cold/warm replicas with a bounded recovery point objective (RPO $\le 60\text{ s}$).

---

## 2. Partition Fencing Architecture

```
                    ┌────────────────────────────────────────────────────────┐
                    │               Host 1: Primary Instance                 │
                    │                                                        │
                    │   Shard 0 Engine ──(Exclusive Lock Held)──┐            │
                    └───────────────────────────────────────────┼────────────┘
                                                                ▼
                                                [/var/data/mdrap/shard_0/shard.lock]
                                                                ▲
                    ┌───────────────────────────────────────────┼────────────┐
                    │   Shard 0 Standby ──(Lock Contention)─────┘            │
                    │   (Exits immediately with code 42)                     │
                    │                                                        │
                    │               Host 2: Warm Standby Instance            │
                    └────────────────────────────────────────────────────────┘
```

### Fencing Enforcement Rules
- **Pre-Initialization Lock**: Before mounting [`src/journal.py`](src/journal.py) or binding TCP ingest sockets, the shard invokes `_acquire_partition_fence()`.
- **Zero Lock Stealing**: If `shard.lock` is held, the instance logs a critical alert and terminates with exit code `42 (ERR_PARTITION_LOCKED)`. Standbys never preemptively steal an active lock without human or orchestrated administrative intervention.
- **Kernel-Guaranteed Clean Release**: Upon unexpected process termination (`SIGKILL`, segfault, kernel panic), the operating system cleans up file descriptor locks automatically, allowing rapid standby restart without manual cleanup.

---

## 3. Monotonic Epoch Generation Counter

To detect and discard network-delayed packets or lingering writes from an evicted primary ("zombie writer"):
1. The partition metadata directory maintains `epoch.seq`.
2. Upon acquiring `shard.lock`, the shard increments the epoch counter:
   $$\text{Epoch}_{t} = \text{Epoch}_{t-1} + 1$$
3. Every IngestLog segment frame and SBE publication header embeds `uint32 epoch`.
4. Downstream consumers and replicas track the highest observed epoch. Frames bearing an older epoch are immediately dropped and routed to the quarantine audit log.

---

## 4. Replication Topology & Guarantees

| Property | Primary Active Engine | Secondary Warm Standby | Cross-DC Replica |
| :--- | :--- | :--- | :--- |
| **Role** | Ingest, Quality, Reconcile, Publish | Tail WAL replica, zero publishing | Offline audit / historical replay |
| **Sync Mechanism** | Direct IngestLog write | Continuous tail of synced WAL segments | Periodic Zstd batch sync via rsync/s3 |
| **RPO (Recovery Point)** | 0 (Atomic commit to disk) | $< 100\text{ ms}$ (LAN replication) | $\le 60\text{ s}$ (WAN archive) |
| **RTO (Recovery Time)** | Immediate | $< 2.0\text{ s}$ (Replay to memory) | $< 10\text{ minutes}$ |
| **Consistency Model** | Strict FIFO monotonic per partition | Sequential follower catch-up | Snapshot consistency |

---

## 5. Failover Procedure & Split-Brain Prevention

1. **Heartbeat Loss**: If the primary shard misses 3 consecutive heartbeats (3,000 ms), the orchestrator triggers failover.
2. **Primary Termination**: The orchestrator issues `SIGTERM` / `SIGKILL` to the primary host to ensure fencing release.
3. **Standby Promotion**: The warm standby attempts `_acquire_partition_fence()`. Upon success, it increments `epoch.seq`.
4. **Replay & Catch-Up**: The standby scans the local WAL tail, verifies CRC32 checksums, rebuilds the in-memory order book and dedup state, and opens consumer sockets.
5. **Consumer Reconnect**: Consumers reconnect and resume sequence consumption with `epoch = epoch_new`.
