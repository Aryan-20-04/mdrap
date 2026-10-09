# MDRAP Phase 9 — Multi-Node Consensus Semantics & Architecture Validation

## 1. Consensus Protocol Formal Classification
In response to the Section 5.1 mandate, this document clarifies the exact nature of MDRAP's consensus implementation:

- **Protocol Classification**: **Quorum-Based Lease Coordination with Monotonic Epoch Write-Path Fencing**.
- **Not Raft / Not Multi-Paxos**: MDRAP does NOT run full in-path Raft state-machine replication for individual market ticks. Replicating every tick via distributed network consensus round-trips would impose a 1–2 millisecond latency tax, destroying low-latency market data economics.
- **Lease-Based Primary-Backup**:
  - A primary node acquires a time-bounded leadership lease (`EpochToken`) issued by a majority quorum ($N/2 + 1$).
  - Events are ingested, normalized, and persisted locally to the authoritative `IngestLog` WAL under this lease.
  - Secondary nodes stream replication logs asynchronously.
  - If the primary crashes or is partitioned, the lease expires, allowing the quorum to promote a secondary with a strictly incremented `epoch`.

## 2. Invariants & Proof of Safety
1. **Quorum Intersection**: Any two majorities in a cluster of size $N$ intersect by at least one node ($\lfloor N/2 \rfloor + 1 + \lfloor N/2 \rfloor + 1 > N$). Conflicting leaders cannot be elected in the same epoch.
2. **Authoritative Write Gate**: Only the leader possessing a valid, non-expired `EpochToken` matching the highest epoch ever seen can append to the WAL.
3. **Partition Behavior**: An isolated leader that cannot reach a majority automatically fails lease renewal (`QuorumLossError`) and abdicates leadership.
