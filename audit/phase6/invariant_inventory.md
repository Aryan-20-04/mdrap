# MDRAP Phase 6 — Platform Safety & Performance Invariant Inventory

## 1. Executive Summary & Purpose
Before expanding system throughput or adding multi-instance topologies, we formalize the platform's non-negotiable safety, correctness, and performance invariants. Any proposed architecture change that compromises an invariant is immediately rejected.

---

## 2. Platform Invariant Inventory

| Invariant ID | Domain | Invariant Definition | Verification Reference |
| :--- | :--- | :--- | :--- |
| **INV-01** | **Sequencing** | Events within an individual sequence domain advance strictly monotonically: $\text{Seq}_{t} = \text{Seq}_{t-1} + 1$. | `tests/test_phase1_identity_sequencing.py` |
| **INV-02** | **Replay Determinism** | Replaying identical raw events (`seed=42`) produces bit-for-bit identical canonical state. | `tests/test_phase1_deterministic_replay.py` |
| **INV-03** | **Durability Boundary** | Events are appended to IngestLog WAL (`events.seg`) with CRC32 prior to SBE frame dispatch. | `tests/test_phase1_wal_integrity.py` |
| **INV-04** | **Quality Hierarchy** | Quality status priority is strictly: `INVALID > SUSPICIOUS > VALID`. Real market volatility triggers `SUSPICIOUS`, never silent discard. | `src/quality.py`, `tests/test_quality.py` |
| **INV-05** | **Zero Silent Loss** | An event is never discarded without explicit accounting. Rejected frames are recorded in `quarantine.db` with 64-bit reason bitmasks. | `tests/test_quarantine.py` |
| **INV-06** | **Cache Immutability** | Reconciler disagreement arbitration must never mutate cached events stored in `self._latest`. | `tests/test_reconciliation.py` |
| **INV-07** | **Bounded Memory** | All queues, ring buffers, and caches have fixed upper bounds (`maxsize=50000`). RSS growth bounded $\le 5\text{ MB}$. | `benchmarks/phase6_scaling_benchmark.py` |
| **INV-08** | **Fan-Out Isolation** | A slow or stalled consumer is evicted after 10 drops; upstream ingestion and peer consumers experience zero backpressure. | `tests/test_phase6_scaling.py` |
| **INV-09** | **Deterministic Revoke**| API keys are indexed by a 64-bit deterministic `key_id = token_hash[:16]`, enabling instant O(1) revocation without collision risk. | `src/security.py`, `tests/test_security.py` |
| **INV-10** | **Secret Redaction** | Diagnostic snapshots recursively scrub all sensitive tokens, passwords, salts, and secrets. | `tests/test_phase5_pilot.py` |

---

## 3. Invariant Preservation Guarantee

Every code addition in Phase 6 (`src/partition.py`) was verified against this inventory. All 10 invariants are mathematically preserved under sharded multi-instance operations.
