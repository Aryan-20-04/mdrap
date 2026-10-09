# MDRAP Phase 7 — Metamorphic Testing Strategy & Invariance Relations

## 1. Executive Summary & Purpose
When processing complex market-data event streams, computing an exact oracle or expected output from scratch can be computationally intensive or prone to tester bias.

**Metamorphic Testing** addresses this by verifying *metamorphic relations* (MRs): input transformations whose expected relation to output transformations is known and guaranteed by system contracts.

---

## 2. Core Metamorphic Relations (MR)

### MR-1: Batch Size Invariance
- **Hypothesis**: Market data state projections (latest BBO, volume-weighted prices, and cumulative volumes) depend solely on the logical ordering of valid events, not on the batch size used by the underlying SQLite drainer or ingest pipeline.
- **Transformation**: Take an identical deterministic stream of $N=1,000$ market events $S$. Run two parallel executions:
  - Run A: Process $S$ with single-item commits (`batch_size=1`).
  - Run B: Process $S$ with batched multi-item commits (`batch_size=100`).
- **Assertion**:
  $$\text{FinalState}_A \equiv \text{FinalState}_B$$
  $$\text{EventCount}_A = \text{EventCount}_B = 1,000$$
  $$\text{VWAP}_A = \text{VWAP}_B$$

### MR-2: Replay-vs-Live Equivalence
- **Hypothesis**: Reading from live network ingestion versus replaying historical records from an IngestLog WAL segment must produce an identical sequence of canonical trade and quote updates.
- **Transformation**:
  - Live Run: Feed simulator streams events directly through the ingest gateway and writes to WAL segment `shard_0.wal`.
  - Replay Run: `BinaryJournalReader` scans `shard_0.wal` from sequence 1 to $N$.
- **Assertion**:
  $$\forall i \in [1, N], \quad \text{ReplayedEvent}(i) \equiv \text{LiveEvent}(i)$$
  Lineage, timestamps, prices, quantities, and validation bitmasks match identically.

### MR-3: Read Idempotency & Non-Mutation
- **Hypothesis**: Repeated queries to storage endpoints, order book snapshots, or sequence monitors must be strictly side-effect-free.
- **Transformation**: Execute query $Q$ once versus executing $Q$ one hundred times.
- **Assertion**: Database checksum, table row counts, and sequence counters remain identical before and after.

---

## 3. Automated Execution in Phase 7
Implemented in [`tests/test_phase7_verification.py`](tests/test_phase7_verification.py):
- `test_metamorphic_batch_size_invariance`: Verifies batch sizes 1, 10, 50, 100 on 500 events yield identical order book and storage projections.
- `test_metamorphic_live_versus_replay_equivalence`: Replays 1,000 serialized WAL events and asserts $100\%$ identity with live ingest records.
- `test_metamorphic_read_idempotency`: Asserts repeated state inspection causes zero sequence advancement or storage mutation.
