# MDRAP Phase 6 — Horizontal Scaling and Multi-Instance Architecture

## 1. Executive Summary & Design Model
MDRAP Phase 6 implements horizontal scaling through **Orthogonal Sharded Instances** (`src/partition.py`). Rather than introducing heavy distributed clustering middleware or clustered coordination networks, the platform scales by executing multiple isolated MDRAP shard instances, each holding authoritative ownership over a defined subset of the symbol universe.

This model adheres strictly to the `/ponytail` design ethos: the simplest solution that works, using standard library multiprocessing, threading, and socket primitives.

---

## 2. Shard Coexistence & Instance Isolation Model

Each shard operates as an autonomous processing domain with dedicated resources:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            MDRAP SHARDED INSTANCE                           │
├───────────────────────────────┬─────────────────────────────────────────────┤
│ Architectural Dimension       │ Shard Isolation Guarantee                   │
├───────────────────────────────┼─────────────────────────────────────────────┤
│ **Identity & Addressing**     │ Shard ID (int), dedicated SBE port (9002+i) │
│ **Feed / Symbol Ownership**   │ Exclusive range: `A-L` (Shard 0), `M-Z` (S1)│
│ **Sequence Domain**           │ Independent monotonic sequence starting at 1│
│ **Persistent Storage**        │ Dedicated WAL directory & SQLite DB file    │
│ **Memory Ring Buffers**       │ Isolated SPSC buffers; zero cross-shard lock│
│ **Failure Blast Radius**      │ A shard crash does not affect other shards  │
└───────────────────────────────┴─────────────────────────────────────────────┘
```

---

## 3. Shard Dispatch and Routing Flow

Incoming feed traffic arrives at the `FleetCoordinator` and is deterministically routed via `SymbolPartitioner`:

1. **Ingress Ingestion**: The router receives raw normalized market events.
2. **Deterministic Hashing**: In $O(1)$ time (< 1.5 µs), the router computes the destination shard:
   $$\text{ShardID} = \text{SymbolPartitioner.get\_shard\_id}(\text{instrument})$$
3. **Non-Blocking Queue Push**: The event is enqueued into the shard's dedicated input queue (`in_queue.put_nowait()`).
4. **Autonomous Ingestion Loop**: The shard thread dequeues, increments its local sequence counter, persists to its dedicated WAL, and broadcasts to its local consumer fan-out manager.

---

## 4. Failure Isolation and Recovery Independence

If Shard 0 experiences a hardware stall, segment corruption, or unexpected restart:
- **Zero Cascading Outage**: Shard 1 continues processing symbols `[M-Z]` at full throughput without a microsecond of pause.
- **Localized Recovery**: Shard 0 executes localized IngestLog recovery on its own segment files (`/var/data/mdrap/shard_0/wal/`) without locking Shard 1's databases.
- **Consumer Notification**: Consumers connected to Shard 0 detect socket disconnection and initiate local reconnection; consumers on Shard 1 experience zero disruption.
