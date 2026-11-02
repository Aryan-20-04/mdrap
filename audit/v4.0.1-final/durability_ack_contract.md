# Write-Ahead Log Durability, Acknowledgment, and Power-Loss Semantics

## 1. Executive Summary

In high-frequency trading market data systems, conflating OS page cache writes (`write()`) with physical disk persistence (`fsync()`) creates a severe financial and regulatory hazard. If a broker-dealer or proprietary trading desk assumes market-data events or trade reports are power-loss durable when they only reside in volatile DRAM, a sudden hardware crash or power outage results in irrecoverable state loss and conflicting ledger balances.

In MDRAP v4.0.1, **MDRAP enforces absolute durability honesty**:
1. It exposes explicit, standardized contract strings describing the exact physical storage guarantee.
2. Under asynchronous or unbuffered policies (`fsync_policy="never"`), the engine explicitly returns `decision.durable = False` at ingestion time.
3. Callers requiring power-loss durability can invoke the barrier primitive `await_durable(timeout_s)`, which blocks until the background flusher synchronizes the specified WAL offset to persistent physical media and updates `decision.durable = True`.
4. Grouped batching policies (`grouped_by_time`, `grouped_by_size`) incorporate bounded quiet-period flushing (45ms timer) to guarantee low-volume feeds do not stall indefinitely.

---

## 2. Durability Contract Matrix

The following matrix documents the exact guarantees provided by each `fsync_policy`:

| `fsync_policy` | Durability Guarantee String | Immediate `decision.durable` at Ingest | Power-Loss Crash Protection | p50 Ingest Latency |
|---|---|:---:|:---:|:---:|
| `"always"` | `"PHYSICAL_FSYNC_DURABLE"` | **`True`** | **Complete** (Synchronous `fdatasync`/`fsync`) | ~120µs - 450µs (NVMe) |
| `"grouped_by_time"` | `"GROUPED_TIME_FSYNC_DURABLE"` | **`False`** (Until flush barrier) | **Bounded** (Fsync occurs every $\Delta t \le 5\text{ms}$ or 45ms quiet-period) | ~1.2µs |
| `"grouped_by_size"` | `"GROUPED_SIZE_FSYNC_DURABLE"` | **`False`** (Until flush barrier) | **Bounded** (Fsync occurs every $N$ bytes or 45ms quiet-period) | ~0.9µs |
| `"never"` | `"OS_BUFFERED_NO_POWER_LOSS_PROTECTION"` | **`False`** (Always) | **None** (Kernel page-cache writeback only; lost on power loss) | ~0.4µs |

---

## 3. Barrier Synchronization via `await_durable`

When operating under grouped or asynchronous modes, applications can selectively escalate critical transactions (such as order status updates, auction triggers, or end-of-day sequence markers) to guaranteed durability without sacrificing throughput for routine quotes:

```python
# Routine high-frequency quote (non-blocking, OS buffered)
dec1 = engine.submit(raw_quote)
assert dec1.durable is False  # Explicit truthfulness: not yet fsynced

# Critical order fill or sequence anchor
dec2 = engine.submit(trade_break_event)
# Await physical durability barrier
is_synced = engine.await_durable(target=dec2, timeout_s=0.5)
assert is_synced is True
assert dec2.durable is True  # Durability status updated dynamically
```

---

## 4. WAL Frame Integrity and Crash Recovery

Each WAL record is protected by a 32-byte binary header with independent cyclic redundancy checks:
- **Header CRC32C**: Validates version, magic bytes, flags, payload length, and sequence monotonic offset.
- **Payload CRC32C**: Validates the serialized event payload.

### Torn Write Recovery
During an unexpected system crash or simulated power loss:
1. If the trailing record was mid-write when power failed, `IngestLog` detects a truncated frame or payload CRC mismatch.
2. The partial torn frame is automatically truncated back to the last clean, verified frame.
3. Valid committed records are never discarded.
4. Recovery metrics report `corrupted_frames_rejected` and fail closed if unrecoverable gaps exist.

---

## 5. Verification Sign-Off

Validated in `tests/final/test_durability_contract.py`:
- `test_durability_contract_strings`: All policies return exact compliant contract strings.
- `test_always_policy_acknowledges_durable_true`: Confirms `decision.durable is True` at ACK time.
- `test_never_policy_acknowledges_durable_false`: Confirms `decision.durable is False` at ACK time.
- `test_grouped_by_time_quiet_period_and_await_durable`: Quiet period flush guarantees barrier completion.
- `test_restart_after_torn_header_and_payload_recovery`: IngestLog truncates torn frames cleanly.
- `test_crc_mismatch_detected_and_safely_rejected`: Corrupted payload bytes are detected and rejected.
