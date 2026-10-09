# MDRAP Phase 7 — Platform Architecture & Invariant Inventory

## 1. Executive Summary & Architectural Scope
MDRAP (Market Data Reliability & Acceleration Platform) sits between raw, noisy, heterogeneous market feeds and downstream trading applications. Its mission is to transform unvalidated market ticks into a fast, canonical, validated, and forensically auditable stream.

This inventory provides an authoritative mapping of modules, critical data paths, and fundamental safety invariants across the system.

---

## 2. Source Code & Interface Topology

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Market Data Ingress                             │
│  • ITCH 5.0 (src/itch.py)          • WebSocket Feeds (src/ws_feed.py)  │
│  • Databento (src/databento_feed.py) • Simulator (src/simulator.py)    │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ RawEvent (monotonic recv_ts)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Gateway & Normalization                            │
│  • src/gateway.py: Validates schemas, stamps wall-clock, emits evt-N  │
│  • src/partition.py: SymbolPartitioner routes by Range / CRC32 Hash    │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ CanonicalEvent
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Quality Evaluation & Rules Engine                    │
│  • src/quality.py & src/rules.py: Evaluates 10 quality checks         │
│  • C Kernel fastpath (src/fastpath.c): High-throughput SBE & rules    │
│  • Status Priority: INVALID > SUSPICIOUS > VALID                      │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ QualityEvaluated Event
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Multi-Feed Reconciliation Engine                     │
│  • src/reconciliation.py: Cross-feed price arbitration & BBO selection │
│  • Provenance Lineage: Preserves source feed & timestamp in decision   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Canonical Decision
                                   ▼
┌──────────────────────────────────┴─────────────────────────────────────┐
│                 Persistence, IPC & Distribution                        │
│  • Durability Boundary: IngestLog WAL (src/journal.py)                 │
│  • Low-Latency IPC: Lock-free SPSC Ring Buffer / SHM (src/shm.py)     │
│  • Historical Query: SQLite 3 Batched Store in WAL mode (src/storage.py)│
│  • Multi-Client Fan-Out: Decoupled bounded queues (src/partition.py)  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Critical Platform Data Paths

| Path Identifier | Entry Point | Processing Stages | Durability Boundary | Egress Point |
| :--- | :--- | :--- | :--- | :--- |
| **DP-HOT-STREAM** | Feed Adapter / Socket | `gateway.ingest` $\rightarrow$ `partition.route` $\rightarrow$ `quality.evaluate` | IngestLog WAL append | SHM Ring Buffer / SBE TCP Socket |
| **DP-STORAGE-DRAIN** | Internal Queue | `storage.drain_batch` $\rightarrow$ `executemany` | SQLite WAL commit | `sqlite3` relational tables |
| **DP-REPLAY-AUDIT** | Sealed WAL Segment | `journal.BinaryJournalReader` $\rightarrow$ CRC32 check | Historical read-only | Verification report / replay stream |
| **DP-FANOUT-CLIENT** | Shard Engine | `ConsumerFanoutManager.enqueue` $\rightarrow$ Bounded Deque | Socket send buffer | Trading consumer socket |

---

## 4. Mandatory Safety Invariants Inventory

| Invariant ID | Formal Guarantee | Architectural Enforcement | Failure Impact |
| :--- | :--- | :--- | :--- |
| **INV-01: NO-SILENT-LOSS** | No valid or invalid event is ever dropped without updating metrics or routing to quarantine. | `gateway.py`, `ws_feed.py`, `partition.py` drop counters | Severe: undetected data loss |
| **INV-02: MONOTONIC-SEQ** | Sequence numbers within a partition never decrement or duplicate. | Monotonic integer sequence generator (`itertools.count`) | Fatal: broken replay ordering |
| **INV-03: QUALITY-HIERARCHY** | Quality status priority is strictly `INVALID > SUSPICIOUS > VALID`. Never downgrade. | Bitmask bitwise OR in `quality.py` and C fastpath | Severe: corrupted execution risk |
| **INV-04: STRICT-LINEAGE** | Every canonical record must trace back to source feed, sequence, and exchange timestamp. | Provenance fields in `CanonicalEvent` and `CanonicalDecision` | High: unprovable trade audit |
| **INV-05: WAL-BEFORE-ACK** | An event is never acknowledged or published until durably appended to IngestLog WAL. | Write-ahead logging sequence in engine hot path | Fatal: phantom trades on crash |
| **INV-06: BOUNDED-MEMORY** | All queues, ring buffers, dedup caches, and fan-out buffers must have finite bounds. | `maxlen` on deques, LRU caches, fixed-size SHM | Fatal: OOM process termination |
| **INV-07: OS-FENCE-EXCLUSIVE** | Exactly one writer process may own a partition directory at any time. | OS kernel file descriptor lock (`shard.lock`) | Fatal: split-brain database corruption |
| **INV-08: PUR-PYTHON-PARITY** | Pure Python engine fallback must yield bit-for-bit identical results to native C kernels. | Dual-implementation regression test matrix | High: subtle cross-platform bugs |
| **INV-09: IMMUTABLE-ARCHIVE** | Historical cold archives must be cryptographically sealed with SHA-256 Merkle proofs. | Merkle tree validation in `journal.py` / `archive.py` | High: regulatory non-compliance |
| **INV-10: DETERMINISTIC-SEED** | Synthetic generators and replay fixtures must produce identical output under fixed seed. | Fixed `seed=42` across all test/benchmark harnesses | Medium: unreproducible test failure |
