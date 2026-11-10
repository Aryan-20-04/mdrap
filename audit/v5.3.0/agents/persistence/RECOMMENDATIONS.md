# Persistence, WAL & Data Integrity — Recommendations

## Priority 0 (Release Blockers)
1. **Fix Replication Server IngestLog Integration (F-PER-01)**:
   - Implement `append_raw(self, frame_bytes: bytes) -> int` on `IngestLog` (`src/mdrap/ingestlog.py`).
   - In `JournalReplicationServer`, verify persistence before sending `MSG_ACK`.
2. **Fix Pruned WAL Replay in `Engine` (F-PER-02)**:
   - In `Engine.recover_projections`, call `self.log.iter_from(start_off)`.
   - In `Engine._replay_projection_batch`, load latest snapshot at or before `from_offset` and replay from snapshot offset.

## Priority 1 (High Priority Fixes)
3. **Add Heartbeat Keepalives to Replication Daemon (F-PER-04)**:
   - Send `MSG_HEARTBEAT` every 500 ms to prevent socket timeout disconnects.
4. **Heal Saturated Queue Gaps in `AsyncProjectionWorker` (F-PER-03)**:
   - Backfill missing offsets from WAL rather than attempting to apply non-contiguous batches.

## Priority 2 (Lineage & Schema)
5. **Wire Lineage Emission in `Engine` (F-PER-05)**:
   - Persist lineage records during `Engine` projection updates.\n