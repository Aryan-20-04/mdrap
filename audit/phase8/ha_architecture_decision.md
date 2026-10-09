# MDRAP Phase 8 — High Availability Architecture Decision Record

## 1. Context & High Availability Requirements
MDRAP operates as an institutional market-data pipeline where data corruption, duplicate event insertion, or conflicting sequences are strictly unacceptable (INV-01, INV-02, INV-07). In a multi-node deployment, host crashes, network partitions, and GC/scheduling pauses can create split-brain conditions where multiple nodes believe they are the authoritative writer.

## 2. Architecture Alternatives Evaluated

| Architecture Option | Strengths | Weaknesses & Disqualifiers | Decision |
|---|---|---|---|
| **Option 1: Distributed Raft Multi-Paxos on Hot Path** | Full active state machine replication across all nodes. | Every tick requires network round-trip consensus before acknowledgement, adding 500–2,000 $\mu$s latency to the hotpath. Violates low-latency mandate. | **REJECTED** |
| **Option 2: Active-Active Dual Feed Ingestion with Dedup** | Instant failover; both nodes ingest concurrently. | Requires 2x feed license costs, high cross-host reconciliation chatter, non-deterministic sequencing order between feeds. | **REJECTED** |
| **Option 3: Primary-Backup with Lease & Epoch Fencing (Chosen)** | Sub-microsecond local hotpath persistence; quorum lease coordination; stale-writer fencing enforced at the WAL boundary. | Backup node lags by replication tail; failover requires lease expiration window. | **ACCEPTED** |

## 3. Decision Specification
MDRAP adopts **Primary-Backup with Quorum Lease Coordination and Write-Path Epoch Fencing**:
1. Single active leader per partition holds a time-bounded lease (`EpochToken`).
2. Quorum consensus ($N/2 + 1$) is required to issue or renew leadership leases.
3. Fencing tokens are enforced strictly at the `IngestLog` WAL boundary (`FencedWALWriter`). Stale leaders attempting post-partition writes are instantly rejected.
