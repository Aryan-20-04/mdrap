# MDRAP v4.0.1 — WAL Durability & Acknowledgement Contract Audit

**Audit Date:** 2026-11-04  
**Target Subsystems:** `src/mdrap/ingestlog.py`, `src/mdrap/engine.py`  
**Raw Test Evidence:** `audit/v4.0.1-final-independent/raw_evidence/p0_durability_suite.log`

---

## 1. Technical Audit of Durability Policies

The MDRAP storage subsystem (`IngestLog`) provides five canonical durability modes:

| Policy | Durability Guarantee String | Immediate `decision.durable` | Write Mechanism | Power-Loss Protection |
|---|---|:---:|---|:---:|
| `"always"` / `"every_event"` | `"PHYSICAL_FSYNC_DURABLE"` | **`True`** | Immediate synchronous `os.fsync()` per event | Complete (Hardware barrier) |
| `"grouped_by_time"` / `"batch"` | `"GROUPED_TIME_FSYNC_DURABLE"` | **`False`** (Until barrier) | Background thread fsyncs every 50ms or quiet period (45ms) | Bounded to 50ms window |
| `"grouped_by_size"` | `"GROUPED_SIZE_FSYNC_DURABLE"` | **`False`** (Until barrier) | Flushes immediately at 64 KB boundary or quiet period (45ms) | Bounded to 64 KB window |
| `"true_group_commit"` / `"adaptive"` | `"GROUP_COMMIT_FSYNC_DURABLE"` | **`True`** (On barrier sync) | Concurrent threads wait on condition variable for shared `fsync()` | Complete (Batch hardware barrier) |
| `"never"` | `"OS_BUFFERED_NO_POWER_LOSS_PROTECTION"` | **`False`** (Always) | Written to OS page cache (`write()`), no `fsync()` | None (Lost on OS/power crash) |

---

## 2. Acknowledgement Lifecycle Specification

```
Caller -> submit(event)
             |
             v
      Acquire engine._lock
             |
             +-> Assign monotonic offset N
             +-> IngestLog.append(event)
             |      |
             |      +-> Write binary frame (Header + CRC32C + Payload + CRC32C)
             |      +-> Flush Python buffer -> OS page cache
             |      |
             |      +-- [If fsync_policy == 'always']:
             |      |      +-> os.fsync(fd)
             |      |      +-> _synced_offset = N
             |      |      +-> Return durable=True
             |      |
             |      +-- [If fsync_policy in ('grouped', 'never')]:
             |             +-> Return durable=False
             v
      Release engine._lock
             |
             v
Caller receives EngineDecision (durable: True/False)
```

### Barrier Synchronization via `await_durable()`
When operating under grouped or asynchronous modes, applications can selectively escalate critical transactions to guaranteed durability without sacrificing throughput for routine quotes:
- `engine.await_durable(decision, timeout_s=0.5)` calls `log.flush()` and checks `synced_offset >= decision.offset`.
- If fsync succeeds, `decision.durable` is updated in-place to `True`.

---

## 3. Discrepancies and Remediations

### Finding D-01: Rejection of Documented Policy Aliases
- **Severity:** P1 (API Compatibility)
- **Confidence:** Confirmed
- **Evidence:** Calling `IngestLog(fsync_policy="every_event")`, `fsync_policy="batch"`, or `fsync_policy="adaptive"` raised:
  `ValueError: Invalid fsync_policy: every_event`
- **Root Cause:** [`src/mdrap/ingestlog.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ingestlog.py#L380) only checked hardcoded strings:
  `("always", "grouped_by_time", "grouped_by_size", "never", "true_group_commit")`.
- **Remediation Implemented:**
  Added alias normalization in `IngestLog.__init__`:
  ```python
  raw_policy = fsync_policy.lower()
  POLICY_ALIASES = {
      "every_event": "always",
      "batch": "grouped_by_time",
      "adaptive": "true_group_commit",
  }
  self.fsync_policy = POLICY_ALIASES.get(raw_policy, raw_policy)
  ```
- **Verification:** All 8 policy names now instantiate and function cleanly with 100% backward compatibility.

---

## 4. Platform-Specific Filesystem Durability Analysis

- **Windows (NTFS):**
  Directory entries (such as `os.replace` on segment creation or snapshot publication) are maintained synchronously by the NTFS transaction log ($LogFile / $MFT). Directory file descriptor fsyncing (`_fsync_dir`) is unnecessary and unsupported on Win32.
- **POSIX (Linux/ext4, macOS/APFS):**
  Renaming or unlinking a file alters the parent directory inode. The implementation calls `_fsync_dir(dirpath)`, which opens the directory `os.O_RDONLY` and invokes `os.fsync(dir_fd)` to prevent directory entry loss across power failures.
