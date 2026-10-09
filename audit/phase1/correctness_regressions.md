# MDRAP Phase 1 — Correctness Regressions & Invariant Proof

**Document Identifier**: `MDRAP-REGR-P1-001`  
**Author**: Principal Systems Engineer & Quantitative Trading Infrastructure Architect  
**Status**: VERIFIED  

---

## 1. Traceability of Correctness Invariants

Every invariant established in `audit/phase0/correctness_contract.md` has been verified by concrete regression tests:

### INV-DUR-001: Explicit Durability & No Silent Downgrades
- **Guarantee**: If durable storage is required by configuration, any storage initialization or append failure must prevent readiness and reject new events rather than falling back to an in-memory buffer.
- **Verification Test**: `tests/test_phase1_persistence_init.py::test_production_mode_storage_failure_blocks_readiness_and_ingest`
- **Proof**: When `Engine.open` raises an `OSError`, `state.engine` remains `None`, `GET /readiness` returns HTTP 503 with `"Durable storage initialization failed"`, and `POST /v1/ingest` returns HTTP 503 with `"Durable storage unavailable"`.

### INV-DUR-002: Bounded WAL Frame Validation & Corruption Rejection
- **Guarantee**: IngestLog segments never attempt unbounded memory allocations on corrupt frame headers.
- **Verification Test**: `tests/test_phase1_wal_integrity.py::test_oversized_frame_length_rejected_without_oom`
- **Proof**: A corrupted frame claiming 2 GB (0x7FFFFFFF) length triggers an immediate `IngestLogCorruptError` without calling `f.read(length)` or raising `MemoryError`.

### INV-DUR-003: Deterministic Live vs Replay Identity
- **Guarantee**: Replaying identical WAL logs from offset 0 produces 100% bit-for-bit identical state and decisions.
- **Verification Test**: `tests/test_phase1_deterministic_replay.py::test_deterministic_replay_and_recovery_metrics`
- **Proof**: Decisions replayed from disk match the live submission in offset, quality status, reason codes, price, and bid/ask prices. `RecoveryMetrics` accurately tracks 3 valid records, 1 quarantined record, and 0 corrupt records.

### INV-SEQ-001: Monotonic Sequence & Restart Identity
- **Guarantee**: Process restarts never generate duplicate event IDs that collide with prior runs.
- **Verification Test**: `tests/test_phase1_identity_sequencing.py::test_event_id_unique_across_process_restarts`
- **Proof**: Distinct Gateway boot runs generate non-overlapping event IDs prefixed with distinct random run identifiers (`evt-{run_id}-{seq}`).

### INV-QUAL-001: Strict Numeric Type & Finite Bounds Safety
- **Guarantee**: Booleans, NaNs, and infinities in price or quantity fields are immediately rejected with `SchemaError` and quarantined.
- **Verification Test**: `tests/test_phase1_input_bounds.py`
- **Proof**: `price=True`, `quantity=False`, `sequence=True`, `price=NaN`, and `price=+Inf` are all proven to be rejected with `SchemaError`.
