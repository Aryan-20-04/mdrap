# MDRAP v4.0.1 Durability Specification

**Specification Version:** 4.0.1-DUR-SPEC  
**Status:** Approved / Enforced  
**Component:** `mdrap.ingestlog.IngestLog`, `mdrap.engine.Engine`  

---

## 1. Scope & Guarantee Matrix

This specification establishes the write-ahead logging (WAL) durability semantics and synchronization policies for MDRAP.

| Fsync Policy | Description | Durability Latency | Data Loss Window | Regulatory Suitability |
| :--- | :--- | :--- | :--- | :--- |
| **`always`** | Immediate `os.fsync()` per append / batch | ~1.5 - 3.0 ms | 0 records | Ultra-strict regulatory audit |
| **`grouped_by_time`** | Background timer fsyncs every $N$ ms (default 10ms) | ~15 - 30 $\mu$s | $\le N$ ms of crash window | High-throughput trading |
| **`grouped_by_size`** | Fsyncs when buffered bytes exceed threshold | ~10 - 25 $\mu$s | $\le M$ bytes of crash window | Burst data pipelines |
| **`never`** | Relies on OS page cache write-back | < 5 $\mu$s | Unbounded OS flush | Benchmarking & replay |

---

## 2. Quiet-Period Flusher Coordinator (`_flusher_loop`)

### 2.1 The Trailing Loss Problem
Under batched or time-grouped fsync policies, if a market feed experiences sudden quiet periods (e.g. market close or network quietness), trailing events written into userspace buffers might not trigger a size threshold or another append call, leaving ACKed events unpersisted.

### 2.2 IngestLog Flusher Loop Mechanics
In `src/mdrap/ingestlog.py`:
1. When `fsync_policy` is `grouped_by_time` or `grouped_by_size`, `IngestLog` launches a daemon thread `_flusher_loop`.
2. The flusher thread checks for pending unpersisted writes:
   $$\text{has\_pending} = (\text{next\_offset} > \text{synced\_offset}) \;\land\; (\text{bytes\_since\_fsync} > 0)$$
3. If pending writes exist and no writes occurred within the quiet window ($> \text{group\_commit\_interval\_ms}$), `_flusher_loop` calls `self.flush()`, issuing `f.flush()` and `os.fsync(f.fileno())`.
4. `self._synced_offset` advances to `_next_offset - 1`.

---

## 3. Explicit Durability Barrier: `await_durable()`

Callers requiring confirmation of physical disk persistence can issue:
```python
# Synchronous barrier on decision durability
engine.await_durable(decision, timeout=2.0)
```
- Returns `True` if `log.synced_offset >= decision.offset`.
- If using `grouped_by_time`, `await_durable` immediately triggers an on-demand flush if not yet synced, ensuring zero latency penalty for critical barrier callers.

---

## 4. Transactional Write Rollback on I/O Error

Every append and batch append records `write_pos = f.tell()` before writing frames.
- If disk is full (`ENOSPC`) or write fails:
  ```python
  except Exception as write_err:
      self._current_file.seek(write_pos)
      self._current_file.truncate(write_pos)
      self._next_offset = start_off
      raise write_err
  ```
- No torn frames or corrupted partial headers remain in the WAL. Monotonic offset sequences are preserved without gaps.
