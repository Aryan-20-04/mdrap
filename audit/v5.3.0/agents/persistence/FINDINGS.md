# Persistence, WAL & Data Integrity — Detailed Findings

## Executive Summary
Audited `storage.py`, `storage_schema.py`, `ingestlog.py`, `journal.py`, `journal_replicator.py`, `projection.py`, `recovery.py`, and quarantine subsystems. WAL framing, length collars, CRC32 v2 hashing, group commit fsync policies, and Merkle quarantine logging are robust. However, 2 CRITICAL defects were discovered: JournalReplicationServer silently drops frames while returning fake ACKs, and Engine recovery crashes on pruned WAL segments due to hardcoded `iter_from(0)`.

## Categorized Findings

### CRITICAL
1. **F-PER-01: Journal Replication Server Silently Drops Frames and Sends Fraudulent ACKs**:
   - `src/mdrap/journal_replicator.py:224-230`: In `_handle_client`:
     ```python
     if msg_type == MSG_FRAME_DATA:
         if self.target_log and hasattr(self.target_log, "append_raw"):
             self.target_log.append_raw(payload)
         ack_frame = pack_replication_frame(MSG_ACK, seq, epoch)
         sock.sendall(ack_frame)
     ```
   - `target_log` is an `IngestLog` instance. `IngestLog` has **no `append_raw` method**.
   - `hasattr()` evaluates to `False`. The server drops the payload without writing it, but sends `MSG_ACK` to the client.
   - **Impact**: Standby node has zero data on failover, causing total data loss.

2. **F-PER-02: Hardcoded `iter_from(0)` in Engine Projection Recovery Crashes on Pruned WAL**:
   - `src/mdrap/engine.py:843, 942, 992, 1017`: Four recovery methods call `self.log.iter_from(0)`.
   - `src/mdrap/ingestlog.py:1488-1493`: Raises `RecoveryError` if `start_offset < segments[0][0]`.
   - When segment retention prunes old segments, `segments[0][0] > 0`.
   - **Impact**: All projection recovery, subscribe, and replay methods crash permanently once pruning has run.

### HIGH
3. **F-PER-03: `AsyncProjectionWorker` Drops Events on Saturated Queue Causing Permanent Assertion Lockout**:
   - `src/mdrap/projection.py:1129-1136`: On `queue.Full`, drops analytical projection events.
   - `src/mdrap/projection.py:581-586`: `SQLiteProjection.apply()` enforces contiguous batch offsets (`batch_start > expected_next` raises `ValueError`).
   - Discards batch after 3 retries; subsequent batches permanently fail because gap is never repaired.

4. **F-PER-04: Replication Daemon Disconnects Every 2 Seconds During Quiet Periods Due to Missing Heartbeats**:
   - `src/mdrap/journal_replicator.py:170, 232`: Server sets 2.0-second socket timeout.
   - Neither client nor server sends keepalive `MSG_HEARTBEAT` frames.
   - **Impact**: Replication link constantly drops and reconnects during quiet trading periods.

### MEDIUM
5. **F-PER-05: Modern `Engine` Architecture Omits Population of `lineage` Table**:
   - In modern `Engine` deployments, `lineage` table remains empty because `EngineDecision` only produces canonical events and quarantine rows.
   - `store.event_lineage()` always returns `None`.

6. **F-PER-06: Individual Batch Write Methods in `Store` Omit Transaction Commits**:
   - `src/mdrap/storage.py:544-614`: `write_canonical_batch`, `write_quarantine_batch`, etc. execute `executemany` without `commit()`.\n