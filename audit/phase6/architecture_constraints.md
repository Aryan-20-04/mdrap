# MDRAP Phase 6 — Architectural Invariants and Scaling Constraints

## 1. Executive Summary
Scaling a high-throughput, low-latency financial market data platform requires adhering to strict mathematical and physical constraints. In distributed systems, attempting to force global total order across independent streams introduces unnecessary coordination bottlenecks and catastrophic failure modes. This document formalizes the architectural constraints that govern any scaling model in Phase 6.

---

## 2. The Core Architectural Invariants

### Invariant 1: Independent Sequence Domain Monotonicity
- **Constraint**: A single exchange feed channel (e.g., NASDAQ ITCH MoldUDP64 channel) possesses a strictly monotonic integer sequence:
  $$S_{k} = S_{k-1} + 1$$
- **Implication**: Events within a single feed channel cannot be partitioned, parallelized, or load-balanced across multiple unordered workers without violating causality and sequence integrity. Ingestion for a specific feed channel must be strictly serialized.

### Invariant 2: Orthogonal Symbol and Venue Independence
- **Constraint**: Orders and trades for `AAPL` on NASDAQ have **zero causal dependency** on trades for `MSFT` on NYSE or `ES` on CME:
  $$\text{Causality}(\text{Event}(S_1), \text{Event}(S_2)) = \emptyset \quad \forall S_1 \neq S_2$$
- **Implication**: Partitioning by **Symbol Universe** or by **Venue / Feed Channel** is mathematically sound. Partitioned instances can execute completely independently with zero inter-instance locks, zero shared state, and zero distributed coordination.

### Invariant 3: Write-Ahead Durability Precedence
- **Constraint**: An event must be committed to the IngestLog WAL (`events.seg`) prior to being emitted to downstream consumers over SBE or shared memory:
  $$\text{CommitTime}(\text{WAL}) \le \text{PublishTime}(\text{SBE})$$
- **Implication**: Any horizontal scaling or multi-instance partitioning must grant each shard local, dedicated ownership of its storage partition to eliminate cross-process disk contention.

### Invariant 4: Zero Distributed Consensus on the Hot Ingest Path
- **Constraint**: Distributed consensus protocols (e.g., Raft, Paxos, Zookeeper) require multiple network round-trips ($> 1\text{ ms}$) to achieve leader quorum.
- **Implication**: Ingesting market data at 10,000+ events/sec with sub-millisecond tail latency ($p99 < 500\text{ \mu s}$) forbids placing consensus handshakes in the tick processing path. Coordination, if used, must be restricted to background fleet control and partition metadata assignment.

### Invariant 5: Strictly Bounded Memory Allocations
- **Constraint**: Unbounded queues or dynamic buffer allocations under market surge conditions cause memory bloat and garbage collection pauses.
- **Implication**: All internal queues (IngestLog backlog, SPSC ring buffers, TCP consumer dispatch buffers) must be bounded with explicit drop or backpressure policies.

---

## 3. Guiding Architectural Axiom for Phase 6

> **"Scale horizontally via independent, partitioned shards with zero-coordination ingestion, dedicated storage ownership, and localized consumer fan-out—never via a centralized distributed database or heavyweight clustered bus."**
