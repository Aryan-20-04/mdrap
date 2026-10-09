# MDRAP Phase 6 — Partition Ownership, Fencing, and Epoch Management

## 1. Executive Summary & Design Invariants
In a partitioned market data platform, split-brain states (where two processes simultaneously believe they own the same partition and write divergent sequence streams) lead to catastrophic data corruption.

MDRAP Phase 6 implements **Exclusive Filesystem Fencing and Monotonic Epoch Numbering**, ensuring that every partition possesses exactly one authoritative writer at any instant in time.

---

## 2. Partition Fencing Architecture

```
[ Primary Shard 0 (Old) ] ──(Partition Lock Held)──> [ /var/data/mdrap/shard_0/shard.lock ]
                                                                   ▲
                                                                   │ (BLOCKED: Lock Contention)
                                                     [ Standby Shard 0 (New Instance) ]
```

### Fencing Enforcement Rules:
1. **Exclusive Lock Acquisition**: Prior to opening any IngestLog WAL segment or SQLite database, the shard process must acquire an exclusive lock on `/var/data/mdrap/shard_<id>/shard.lock`.
2. **Immediate Fail-Closed on Collision**: If another process holds the lock, the second instance exits immediately with exit code `42 (ERR_PARTITION_LOCKED)`. It never falls back to uncoordinated shared writes.
3. **OS-Level Cleanup**: On process crash or termination, the operating system kernel automatically releases the file descriptor lock, allowing rapid failover without manual tombstone deletion.

---

## 3. Monotonic Epoch Generation Counter

To detect and reject stale messages from an orphaned writer:
1. Every shard directory maintains an integer `epoch.seq` file.
2. Upon acquiring the partition lock, the shard increments the epoch:
   $$\text{Epoch}_{t} = \text{Epoch}_{t-1} + 1$$
3. Every IngestLog segment file records the current `epoch` in its binary header.
4. If a consumer or reader detects a frame with an older epoch than currently observed, the frame is rejected as a stale replay artifact.
