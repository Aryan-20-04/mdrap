# MDRAP v4.0.0 — Phase 0 Defect Reproduction Register (E1–E15)

All findings from Audit Plan v1.1 have been evaluated via automated test suite `tests/test_audit_v11_reproductions.py`:

| Finding | Title | Status | Evidence | Anchor File & Lines |
|:---|:---|:---:|:---|:---|
| **E1** | Oversize payload accounting / WAL mismatch | **Independently Reproduced** | `test_e1_oversize_payload_accounting_wal_mismatch` passes | `src/mdrap/engine.py:708-750` |
| **E2** | Large int64 sequence poisons later checks | **Independently Reproduced** | `test_e2_large_int64_sequence_poisons_later_checks` passes | `src/mdrap/quality.py:448-477` |
| **E3** | Old retransmission marked suspicious, not duplicate | **Independently Reproduced** | `test_e3_old_retransmission_classified_suspicious_not_duplicate` passes | `src/mdrap/quality.py:490-501` |
| **E4** | Price corridor fails to recover after +20% repricing | **Independently Reproduced** | `test_e4_price_corridor_reseed_failure_on_rerate` passes | `src/mdrap/quality.py:663-686` |
| **E5** | Quote dedup drops size-only updates | **Independently Reproduced** | `test_e5_quote_dedup_drops_size_only_changes` passes | `src/mdrap/engine.py:1570-1579` |
| **E6** | Recovery invokes `on_gap` without new traffic | **Independently Reproduced** | `test_e6_recovery_invokes_on_gap_without_new_traffic` passes | `src/mdrap/engine.py:1509-1536` |
| **E7** | `session_id` omitted from WAL serialization | **Independently Reproduced** | `test_e7_session_id_absent_from_wal_serialization` passes | `src/mdrap/ingestlog.py:774-779` |
| **E8** | `grouped_by_time` direct append unsynced when quiet | **Independently Reproduced** | `test_e8_grouped_by_time_direct_append_unsynced_when_quiet` passes | `src/mdrap/ingestlog.py:833-839` |
| **E9** | SIGKILL / process crash durability | **Positive Control** | `test_e9_positive_control_process_crash_durability` passes | `src/mdrap/ingestlog.py:824` |
| **E10** | Mid-segment bit flip causes open to fail closed | **Positive Control** | `test_e10_positive_control_mid_segment_bit_flip` passes | `src/mdrap/ingestlog.py:1395` |
| **E11** | Snapshot offset derived from event_count skips WAL events | **Independently Reproduced** | `test_e11_snapshot_offset_skips_wal_events` passes | `src/mdrap/engine.py:1997` |
| **E12** | `Engine.submit` uses `append_batch`, forcing synchronous fsync | **Independently Reproduced** | `test_e12_engine_submit_append_batch_forces_fsync` passes | `src/mdrap/ingestlog.py:968-974` |
| **E13** | Snapshot holds lock across disk I/O | **Independently Reproduced** | `test_e13_snapshot_holds_lock_measurement` passes | `src/mdrap/engine.py:1994-2008` |
| **E14** | `json.dumps` upfront payload size check | **Independently Reproduced** | `test_e14_json_dumps_overhead_in_submit` passes | `src/mdrap/engine.py:703` |
| **E15** | Projection failure causes partial commits and retry duplication | **Independently Reproduced** | `test_e15_projection_failure_causes_partial_side_effects` passes | `src/mdrap/engine.py:767-803` |
