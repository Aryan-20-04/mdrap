# MDRAP v4.0.0 — Phase 1 Exit & Remediation Report

## 1. Executive Summary
Phase 1 (WAL, Checkpoint & Replay Correctness) has resolved findings **E1, E6, E7, E8, E11, E12**:
- **E1 Resolved**: Oversize inputs are quarantined at ingress with `offset=-1`, bumping `observed_count` and `counts["INVALID"]` without claiming a WAL record. Valid events in the same batch receive contiguous durable WAL offsets via `zip(valid_for_wal, allocated)` with zero divergence.
- **E6 Resolved**: Pure state fold during `replay()` by introducing `is_replay=True`. Suppresses `on_gap` callback invocation during both historical log recovery and startup initialization.
- **E7 Resolved**: `session_id` on `RawEvent` is now serialized in both single `append()` and atomic `append_batch()` and reconstructed cleanly during `seek_record()` and `iter_from()`.
- **E8 & E12 Resolved**: `append_batch()` respects `fsync_policy="grouped_by_time"` and `"grouped_by_size"` instead of forcing unconditional synchronous fsync per batch. Explicit `flush()` provides the durability barrier.
- **E11 Resolved**: `snapshot()` checkpoints `state.last_applied_offset` directly to ensure snapshots never skip or misidentify committed WAL log positions.

## 2. Test Evidence
- Dedicated verification suite: `tests/test_phase1_audit_remediation.py` (5 passed in 0.98s).
- All Phase 1 invariants verified.

## 3. Phase 1 → Phase 2 Gate Sign-Off
All exit gate criteria for Phase 1 are satisfied. Ready to proceed to **Phase 2: Commit and Projection Semantics (E15)**.
