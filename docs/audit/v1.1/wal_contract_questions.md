# MDRAP v4.0.0 — Phase 0 WAL & Recovery Contract Design Note

Answers to mandatory Phase 1 design questions (Playbook §3):

1. **What uniquely identifies an event across feed, session and reconnect?**
   - Triplet: `(source, session_id, sequence_number)` for sequenced feeds; `(source, instrument_id, exchange_timestamp, raw_id)` for unsequenced feeds.
2. **Does every accepted event get exactly one WAL record? How are rejected events accounted for?**
   - Yes. Only events passing ingress sizing and schema gates enter the WAL. Oversize/malformed events are recorded in `quarantine` with ingress counter `observed_count` incremented, but consume no WAL record offset.
3. **Are WAL offsets byte offsets, record indexes, or sequence numbers?**
   - WAL offsets are monotonic 64-bit record indexes (`0, 1, 2, ...`). Frame headers and segment indexes translate record offsets to byte positions.
4. **What does `applied_offset` mean, and how is it advanced contiguously?**
   - The highest contiguous record index folded into `EngineState`. It advances only after in-memory state transition succeeds.
5. **What exact WAL position does a snapshot represent?**
   - The snapshot records `last_applied_wal_offset` and `wal_segment_id`. It represents the state after applying that exact record.
6. **How are snapshot and WAL segment rotation coordinated?**
   - A snapshot may only clean up WAL segments whose maximum record offset is `< snapshot.last_applied_wal_offset`.
7. **What fields are required to replay the event deterministically?**
   - `raw_id`, `source`, `session_id`, `receive_timestamp`, and full wire `payload`.
8. **What does each fsync policy guarantee?**
   - `always`: Process crash, OS crash, and host power loss (subject to drive write cache).
   - `grouped_by_size` / `grouped_by_time`: Durable up to last barrier; bounded window of uncommitted data on power loss.
   - `none`: Volatile memory only.
9. **What happens if WAL append succeeds but state fold fails?**
   - WAL contains the record. On replay/restart, state machine folds the record or logs an unrecoverable poison error.
10. **What happens if state fold succeeds but a projection fails?**
    - Authoritative state remains intact. Projections must be decoupled consumers tracking their own `consumer_offset` and retrying idempotently.
11. **Can replay trigger callbacks, network operations, notifications or timer actions?**
    - Strictly NO. Pure state fold during replay. External side effects (`on_gap`, alerts, webhooks) must be suppressed.
