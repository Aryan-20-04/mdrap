# Persistence, WAL & Data Integrity — Exact Evidence

## 1. Replication Silent Drop
- File: `src/mdrap/journal_replicator.py:224-230`
  ```python
  if msg_type == MSG_FRAME_DATA:
      if self.target_log and hasattr(self.target_log, "append_raw"):
          self.target_log.append_raw(payload)

      # Return ACK
      ack_frame = pack_replication_frame(MSG_ACK, seq, epoch)
      sock.sendall(ack_frame)
  ```
- File: `src/mdrap/ingestlog.py`
  (Method `append_raw` is completely absent from `IngestLog` class).

## 2. Hardcoded `iter_from(0)` in Engine
- File: `src/mdrap/engine.py:942`
  ```python
  for off, raw in self.log.iter_from(0):
      if off < start_off:
  ```
- File: `src/mdrap/ingestlog.py:1488-1493`
  ```python
  if self.next_offset > 0 and segments and start_offset < segments[0][0]:
      raise RecoveryError(f"WAL history incomplete: requested start_offset {start_offset}...")
  ```

## 3. Projection Gap Lockout
- File: `src/mdrap/projection.py:581-586`
  ```python
  if batch_start > expected_next:
      raise ValueError(f"Projection gap detected for '{self.name}': expected start offset <= {expected_next}, got batch_start {batch_start}")
  ```\n