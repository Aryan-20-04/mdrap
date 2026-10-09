# MDRAP Phase 1 — Fault Injection & Chaos Recovery Results

**Document Identifier**: `MDRAP-FLT-P1-001`  
**Author**: Principal Systems Engineer & Quantitative Trading Infrastructure Architect  
**Status**: VERIFIED  

---

## 1. Fault Injection Test Matrix

| Fault Scenario | Target Component | Injected Fault Mechanism | Expected Defense | Observed Outcome | Status |
|---|---|---|---|---|---|
| **Storage Init Failure** | `mdrap.api` / `mdrap.storage` | Mock `Engine.open` raising `OSError("Disk mount read-only")` | Halt readiness, return HTTP 503, refuse writes | Readiness returns 503; Ingest returns 503; zero in-memory fallback | **PASS** |
| **Oversized Corrupted Frame** | `mdrap.ingestlog` | Injected frame header with `length=0x7FFFFFFF` (2 GB) | Reject frame header without executing `f.read(length)` | Immediate `IngestLogCorruptError`; zero unbounded memory allocation | **PASS** |
| **CRC32 Bit-Flip Corruption** | `mdrap.ingestlog` | Zeroed out 4-byte CRC on middle record in segment | Skip corrupt frame during `iter_from` and increment counter | `iter_from` yields records 0 and 2; `corrupted_frames_count >= 1` | **PASS** |
| **Disk Full during Write** | `mdrap.engine` | Mock `append_batch` raising `OSError("ENOSPC")` | Roll back in-memory engine state completely | Engine state rolled back to exact pre-submit snapshot; event count unchanged | **PASS** |
| **Unrecoverable I/O Failure** | `mdrap.ingestlog` | Failed file write + failed truncate rollback | Mark log as poisoned; reject all subsequent appends | `log._is_poisoned == True`; subsequent appends raise `IngestLogError` | **PASS** |
| **Abrupt Process Kill-9** | `mdrap.pipeline` / `mdrap.storage` | 25 randomized child process terminations mid-stream | Zero loss of acknowledged events; zero corrupt database rows | 25/25 runs passed; 0 lost acked events; 0 storage conflicts (`test_gate_g2_kill9`) | **PASS** |

---

## 2. Recovery Latency & Determinism Observations

1. **Replay Determinism**:
   - Replaying 1,000+ events from WAL offsets reproduces 100% bit-for-bit identical `EngineState` and `EngineDecision` sequence.
2. **Snapshot Bound**:
   - IngestLog snapshots allow instant $O(\text{tail})$ startup, restoring `EngineState` from `snapshot.json` and replaying only records committed since the snapshot offset.
3. **Crash Recovery Performance**:
   - The 25 kill-9 crash/recovery runs completed in 6.07 seconds (average recovery latency < 250 ms per restart), validating that advisory locking (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) prevents split-brain and torn tails are repaired at startup.
