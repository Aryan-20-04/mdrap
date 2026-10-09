# MDRAP Phase 6 — Distributed Consistency & Data Ordering Semantics

## 1. Executive Summary & Semantic Contract
In distributed architectures, imprecise claims about consistency (e.g., claiming "exactly-once delivery" or "global total order" without formal proof) lead to catastrophic trading desk failures.

This document formally specifies the end-to-end data delivery and ordering guarantees provided by MDRAP Phase 6 under sharded multi-instance operations.

---

## 2. Formal Semantic Delivery Matrix

| Semantic Dimension | Supported Guarantee | Guarantee NOT Provided | Operational Justification |
| :--- | :--- | :--- | :--- |
| **Ordering Scope** | **Per-Partition Monotonic Ordering** | Global Cross-Partition Total Order | Distinct symbols (`AAPL` vs `MSFT`) have zero causal ordering dependency. |
| **Delivery Guarantee**| **At-Least-Once Delivery** | Network Exactly-Once Delivery | Network packets can drop or duplicate; consumers deduplicate via sequence numbers. |
| **Durability Boundary**| **Committed WAL Persistence** | In-Memory-Only Acknowledgment | Events are fsynced to `events.seg` before SBE publication. |
| **Replay Semantics** | **Deterministic Monotonic Replay** | Arbitrary Out-of-Order Backfill | Historical replay streams records strictly in ascending sequence order. |
| **State Mutability** | **Append-Only Immutable Ledger** | In-Place State Mutation | Database records and WAL segments are strictly append-only. |

---

## 3. The Per-Partition Monotonic Ordering Theorem

For any two events $E_1$ and $E_2$ belonging to the same partition key $P$:

$$\text{If } \text{Sequence}(E_1) < \text{Sequence}(E_2), \text{ then } \text{DeliveryTime}(E_1) < \text{DeliveryTime}(E_2)$$

- Every consumer subscribed to partition $P$ is guaranteed to receive events in strictly ascending sequence order: $1, 2, 3, \dots, N$.
- If a consumer detects a sequence delta $\Delta_{\text{seq}} = \text{Seq}_{t} - \text{Seq}_{t-1} > 1$, it triggers a localized gap recovery query (`replay --from-seq`) to retrieve missing frames from the shard's IngestLog WAL.
