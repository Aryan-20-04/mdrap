# MDRAP Phase 6 — Partitioning Contract & Sequence Domain Specification

## 1. Executive Summary & Purpose
This document establishes the binding technical contract for partitioning market data across independent shard instances in MDRAP. It defines partition key derivation, sequence domain scoping, state ownership boundaries, and rebalancing constraints.

---

## 2. Partitioning Specifications

### 2.1 Partition Key Derivation
The canonical partition key is the normalized **Instrument Symbol** (`instrument_id`):

$$\text{PartitionKey} = \text{NormalizeSymbol}(\text{raw\_event.instrument})$$

Routing to shard index $i \in \{0, 1, \dots, K-1\}$ utilizes either deterministic range boundaries or cyclic redundancy hashing:

- **Range Partitioning (Default for Production Desks)**:
  - Shard 0: Symbols beginning with `[A-L]` (e.g., `AAPL`, `AMZN`, `GOOGL`).
  - Shard 1: Symbols beginning with `[M-Z]` (e.g., `MSFT`, `NVDA`, `TSLA`).
- **Consistent Hash Partitioning (Alternative for Uniform Distribution)**:
  $$\text{ShardID} = \text{CRC32}(\text{instrument\_id}) \pmod K$$

### 2.2 Sequence Domain Scoping
- **Contract**: Sequence numbers are strictly **Per-Partition Monotonic**:
  $$\text{Seq}_{\text{shard}, t} = \text{Seq}_{\text{shard}, t-1} + 1$$
- **Cross-Partition Disclaimer**: The platform explicitly **does not guarantee** total causal order across different partitions. A trade in `AAPL` (Shard 0) with sequence 100 has no causal ordering relationship with a trade in `MSFT` (Shard 1) with sequence 100.
- **Consumer Identification**: Every SBE frame emitted includes a 16-bit `shard_id` in the header, enabling consumers to track independent sequence continuity per shard.

---

## 3. State & Persistence Ownership Boundaries

1. **Memory Isolation**:
   - In-memory order books (`src/depth.py`), Welford statistical moments (`src/quality.py`), and dedup caches are 100% private to the shard instance.
   - Zero shared memory or thread locks exist across shards.
2. **Persistence Isolation**:
   - Each shard writes to its own isolated directory:
     - `/var/data/mdrap/shard_0/wal/events.seg`
     - `/var/data/mdrap/shard_0/db/canonical.db`
     - `/var/data/mdrap/shard_1/wal/events.seg`
     - `/var/data/mdrap/shard_1/db/canonical.db`
   - SQLite single-writer lock contention is completely eliminated between partitions.

---

## 4. Rebalancing & Migration Invariants

- **Trading Session Freeze**: To prevent split-brain states or sequence gaps, **rebalancing is strictly prohibited during active market trading hours**.
- **Cold Rebalancing Protocol**:
  1. Halt incoming feed ingestion during EOD maintenance window.
  2. Flush and checkpoint all shard IngestLog WAL files.
  3. Recompute partition hash map or range boundaries in `config.json`.
  4. Restart shards with new symbol assignments.
  5. Consumers re-read partition routing table on reconnection.
