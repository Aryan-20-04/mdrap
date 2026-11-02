# Projection Monotonic Ordering and Strict Checkpoint Initialization

## 1. Executive Summary

Projections in event-sourced architectures translate raw or normalized Write-Ahead Log events into material views (such as current order books, VWAP curves, or trade ledgers).

Prior to MDRAP v4.0.1, the checkpoint check contained a permissive flaw:
```python
cur_checkpoint < 0 OR (batch_end >= cur_checkpoint AND batch_start <= cur_checkpoint + 1)
```
When `cur_checkpoint < 0` (an uninitialized database), the check would accept a batch starting at **any arbitrary offset** (such as offset 1 or offset 100), bypassing initial event validation. If initial events were lost or delivered out of sequence, the projection would start mid-stream with corrupted state.

In MDRAP v4.0.1, **strict zero-based checkpoint initialization and contiguous gap rejection are strictly enforced**:
1. An uninitialized projection (`cur_checkpoint == -1`) strictly requires `batch_start == 0`.
2. Any first batch starting at `batch_start > 0` is rejected with `ValueError("First batch must start at offset 0")`.
3. Checkpoint advancement across missing middle offsets is rejected with `ValueError("Non-contiguous batch")`.
4. Checkpoints are committed in the same database transaction as the projection records.

---

## 2. Checkpoint Validation Rules

| Current Checkpoint State | Incoming Batch Offsets | Result | Invariant Enforced |
|---|---|:---:|---|
| `cur_checkpoint == -1` | `[0, 1, 2]` | **Accepted** | Valid initial sequence from offset 0 |
| `cur_checkpoint == -1` | `[1, 2, 3]` | **REJECTED** | Rejects missing initial offset 0 |
| `cur_checkpoint == -1` | `[100, 101]` | **REJECTED** | Rejects unanchored stream injection |
| `cur_checkpoint == 5` | `[6, 7, 8]` | **Accepted** | Contiguous progression ($5 + 1 = 6$) |
| `cur_checkpoint == 5` | `[7, 8, 9]` | **REJECTED** | Rejects missing middle offset 6 |
| `cur_checkpoint == 5` | `[4, 5, 6, 7]` | **Accepted** | Idempotent overlap: records 4..5 ignored, 6..7 committed, checkpoint $\rightarrow$ 7 |
| `cur_checkpoint == 5` | `[3, 4, 5]` | **Accepted** | Complete duplicate: no-op, checkpoint remains 5 |

---

## 3. Atomic Transaction Commit

To ensure checkpoint consistency across system restarts and sudden process termination, `SQLiteProjection.apply()` commits both data records and the latest checkpoint within a single transaction:

```sql
BEGIN IMMEDIATE;
-- Insert canonical event records into projections table
INSERT OR REPLACE INTO canonical_projections (offset, source, instrument, price, quantity, ...)
VALUES (?, ?, ?, ?, ?, ...);

-- Record checkpoint offset in the exact same transaction
INSERT OR REPLACE INTO _mdrap_checkpoints (projection_name, checkpoint_offset, updated_at)
VALUES ('default', ?, ?);
COMMIT;
```

---

## 4. Verification Evidence

Validated in `tests/test_final_p0a_projection_checkpoint.py`:
- `test_first_batch_starts_at_zero_accepted`: Offset 0..9 accepted and checkpointed to 9.
- `test_first_batch_starts_at_one_rejected`: Offset 1..9 raises `ValueError`.
- `test_first_batch_starts_at_one_hundred_rejected`: Offset 100 raises `ValueError`.
- `test_missing_middle_offset_rejected`: Batch 0..4 followed by batch 6..10 raises `ValueError`.
- `test_overlapping_batch_is_idempotent`: Batch 0..4 followed by batch 3..7 commits records 5..7 idempotently and advances checkpoint to 7.
- `test_duplicate_batch_delivery`: Repeated delivery of batch 0..4 produces identical query state with zero duplicates.
- `test_process_restart_with_incomplete_projection`: Uncommitted batch is recovered from WAL on restart.
