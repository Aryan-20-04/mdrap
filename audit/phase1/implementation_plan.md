# MDRAP Phase 1 — Implementation Plan: Core Correctness, Durability & Recovery Remediation

**Document Identifier**: `MDRAP-PLAN-P1-001`  
**Author**: Principal Systems Engineer & Quantitative Trading Infrastructure Architect  
**Input Baseline**: Phase 0 Baseline Audit (`audit/phase0/`)  
**Objective**: Establish complete institutional trustworthiness for core event processing, WAL persistence, crash recovery, and health reporting before performance optimization or SDK expansion.

---

## 1. Traceability: Phase 0 Findings to Remediation Workstreams

| Phase 0 Finding / Defect | Invariant | Remediation Task | Affected Source Files | Target Verification Test |
|---|---|---|---|---|
| Silent durability downgrade to in-memory on WAL failure | `INV-DUR-001` | **Task 1: Remediation A** | `src/mdrap/api.py`, `src/mdrap/service.py`, `src/mdrap/storage.py` | `tests/test_phase1_persistence_init.py` |
| Unbounded WAL frame length read & silent record skipping | `INV-DUR-002` | **Task 2: Remediation B** | `src/mdrap/ingestlog.py` | `tests/test_phase1_wal_integrity.py` |
| Undocumented WAL acknowledgement boundary states | `INV-DUR-001` | **Task 2: Remediation B** | `src/mdrap/ingestlog.py`, `src/mdrap/engine.py` | `tests/test_phase1_ack_semantics.py` |
| Event ID collision across process restarts (`evt-1`) | `INV-SEQ-001` | **Task 3: Remediation C** | `src/mdrap/gateway.py`, `src/mdrap/models.py` | `tests/test_phase1_identity_sequencing.py` |
| Trapped out-of-order sequence gap buffer in QualityEngine | `INV-SEQ-002` | **Task 3: Remediation C** | `src/mdrap/quality.py` | `tests/test_phase1_sequence_gaps.py` |
| Replay recovery skipping corrupt records silently | `INV-DUR-003` | **Task 4: Remediation D** | `src/mdrap/engine.py`, `src/mdrap/ingestlog.py` | `tests/test_phase1_deterministic_replay.py` |
| Async storage writer queue dropping in-flight batches on kill | `INV-DUR-001` | **Task 5: Remediation E** | `src/mdrap/pipeline.py`, `src/mdrap/engine.py` | `tests/test_phase1_exception_safety.py` |
| Sequential TCP client broadcast loop stall | `INV-OPS-001` | **Task 5: Remediation E** | `src/mdrap/gateway_tcp.py` | `tests/test_phase1_tcp_concurrency.py` |
| Boolean & NaN values bypassing gateway validation | `INV-QUAL-001` | **Task 6: Remediation F** | `src/mdrap/gateway.py`, `src/mdrap/models.py` | `tests/test_phase1_input_bounds.py` |
| Static API key salt & unauthenticated path disclosure | `INV-OPS-002` | **Task 1: Remediation A** | `src/mdrap/api.py`, `src/mdrap/security.py` | `tests/test_api_auth.py` |

---

## 2. Dependency-Ordered Task List

### Task 1: Remediation A — Persistent-Engine Initialization & Explicit Modes
- **Problem**: API and service initialization paths silently fell back to an in-memory engine when WAL or database initialization failed, while reporting `"ready"` and `"healthy"`.
- **Target Changes**:
  1. Define explicit persistence modes in `src/mdrap/storage.py` and `src/mdrap/api.py`:
     - `PRODUCTION_DURABLE` (mandatory WAL + SQLite; failure aborts startup or forces `readiness=False`).
     - `DEVELOPMENT_IN_MEMORY` (allowed only when `path=":memory:"` or explicitly configured via `MDRAP_DEV_MODE=1` or `persistence_mode="development"`).
     - `DEGRADED` (explicitly flagged, observable in telemetry, rejects new writes if storage is unwritable).
  2. In `AppState.__init__` (`src/mdrap/api.py`):
     - Remove silent `except Exception: fallback to in-memory Engine` block.
     - If durable storage initialization fails in production mode, store the initialization failure error in `self.init_error` and keep `engine=None`.
  3. In `get_readiness` (`src/mdrap/api.py`):
     - If `self.init_error` is set, return HTTP 503 with exact failure details.
  4. In `get_health` (`src/mdrap/api.py`):
     - Report `persistence_mode` (`"production_durable"` vs `"development_in_memory"`).
     - Redact full filesystem directories from public output.
- **Acceptance Criteria**: Simulated storage failure in production mode never serves events in an in-memory fallback; readiness returns 503; dev mode works when explicitly declared.

### Task 2: Remediation B — WAL Integrity, Frame Validation & Acknowledgement Semantics
- **Problem**: IngestLog frame length was unchecked against memory bounds prior to `f.read(length)`, risking OOM on corrupt headers. Acknowledgement semantics between userspace write, OS buffer, and `fsync` were implicit.
- **Target Changes**:
  1. Define `AckStatus` enum in `src/mdrap/ingestlog.py`:
     - `RECEIVED = 1`: Packet reached network socket / function parameter.
     - `ACCEPTED = 2`: Validated against schema and bounds.
     - `BUFFERED_APP = 3`: Staged in userspace write buffer.
     - `WRITTEN_OS = 4`: Flushed to OS kernel buffer cache via `write()` + `flush()`.
     - `DURABLY_COMMITTED = 5`: Flushed and synchronized to physical media via `os.fsync()`.
     - `RECOVERED = 6`: Reconstructed from WAL log replay after restart.
  2. Implement strict frame length bounds:
     - `MAX_FRAME_PAYLOAD_BYTES = 16 * 1024 * 1024` (16 MB hard ceiling).
     - In `_verify_and_repair_segment` and `iter_from`: if `length > MAX_FRAME_PAYLOAD_BYTES` or `length > self.max_segment_bytes`, treat frame as corrupt/torn rather than executing an unbounded allocation.
  3. Poisoning & Failure Handling:
     - When `os.fsync()` fails or disk write raises `OSError`/`IOError`, mark `self._is_poisoned = True` and fail subsequent append attempts until explicit reopen or recovery.
- **Acceptance Criteria**: Corrupted frame lengths cannot cause memory exhaustion; disk-full or sync failure prevents subsequent false durable acks.

### Task 3: Remediation C — Event Identity, Monotonic Sequencing & Restart Boundaries
- **Problem**: Monotonic counters (`itertools.count(1)`) reset across process restarts, risking duplicate event IDs. Out-of-order jumps buffered in `QualityEngine` could be trapped indefinitely if callers omitted `drain_expired()`.
- **Target Changes**:
  1. In `src/mdrap/gateway.py`:
     - Ensure `next_raw_id()` and `next_event_id()` incorporate the 64-bit random boot run identifier: `evt-{run_id}-{seq}`.
  2. In `src/mdrap/quality.py`:
     - In `evaluate()`: when an event sequence gap occurs, buffer event with a deadline timestamp. If `sl.pending` has entries older than `cfg.reorder_window_s` or if buffer reaches capacity, automatically release expired ticks rather than requiring external polling.
- **Acceptance Criteria**: Restarts never duplicate event IDs; out-of-order ticks are guaranteed to emit within `reorder_window_s`.

### Task 4: Remediation D — Deterministic Replay, Snapshot Restoration & Recovery Metrics
- **Problem**: Recovery must guarantee that replaying the same event log reproduces identical decisions and state counts, and any skipped corrupted records must be strictly counted and surfaced.
- **Target Changes**:
  1. In `src/mdrap/engine.py`:
     - Add `RecoveryMetrics` dataclass tracking `replayed_records`, `valid_records`, `quarantined_records`, `corrupted_frames`, `skipped_offsets`.
     - In `Engine.replay()`: return `(decisions, recovery_metrics)`.
     - Ensure snapshot save/restore produces bit-for-bit identical `EngineState`.
  2. In `src/mdrap/ingestlog.py`:
     - In `iter_from()`: yield corrupt frame warnings to a caller-supplied error handler or track corruption counters rather than silently suppressing them with `continue`.
- **Acceptance Criteria**: Replaying identical WAL logs produces 100% identical canonical outcomes; snapshot + incremental replay matches full replay from offset 0.

### Task 5: Remediation E — Exception Safety & State Consistency
- **Problem**: Updating state counters before durable write confirmation causes divergence if the storage write fails.
- **Target Changes**:
  1. In `src/mdrap/engine.py` `submit()`:
     - Ensure `self.state.event_count` and `self.state.counts` are incremented *only after* WAL `append_batch()` succeeds.
     - If `self.log.append_batch()` raises an exception, the in-memory engine state remains unmodified (transaction rollback).
  2. In `src/mdrap/gateway_tcp.py`:
     - Ensure `broadcast()` uses concurrent non-blocking client queues or `asyncio.gather` with per-client timeouts so a stalled client cannot freeze the server event loop.
- **Acceptance Criteria**: Injecting an I/O exception during WAL append leaves engine event counters completely unchanged.

### Task 6: Remediation F — Core Input Bounds & Numerical Type Safety
- **Problem**: In Python, `isinstance(True, int)` is `True`, and IEEE-754 `NaN` bypasses `< 0` comparisons.
- **Target Changes**:
  1. In `src/mdrap/gateway.py` and `src/mdrap/models.py`:
     - Strictly reject `isinstance(val, bool)` for prices, quantities, sequence numbers, and timestamps.
     - Validate `math.isfinite(val)` and `val > 0` for trade prices and quantities.
     - Reject oversized payloads (> 1 MB) before deserialization or parsing.
- **Acceptance Criteria**: Booleans, `NaN`, `+Inf`, and `-Inf` in numerical fields are immediately quarantined with `Reason.SCHEMA_VIOLATION`.

---

## 3. Test & Verification Plan

1. **Unit & Focused Tests**:
   - `tests/test_phase1_persistence_init.py`
   - `tests/test_phase1_wal_integrity.py`
   - `tests/test_phase1_identity_sequencing.py`
   - `tests/test_phase1_deterministic_replay.py`
   - `tests/test_phase1_exception_safety.py`
   - `tests/test_phase1_input_bounds.py`
2. **Fault-Injection Tests**:
   - Mock disk-full on WAL write.
   - Inject corrupted CRC32 into middle segment.
   - Inject torn tail at segment EOF.
   - Inject corrupted frame length (0x7FFFFFFF).
   - Abrupt shutdown during batch write.
3. **Full Regression Execution**:
   - Run the 11-stage baseline suite to prove zero regression across all 1,041 tests.
