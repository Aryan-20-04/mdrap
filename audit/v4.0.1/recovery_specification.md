# MDRAP v4.0.1 Recovery Specification

**Specification Version:** 4.0.1-REC-SPEC  
**Status:** Approved / Enforced  
**Component:** `mdrap.engine.Engine`, `mdrap.ingestlog.IngestLog`  

---

## 1. Scope & Invariants

This specification defines the authoritative crash recovery and snapshot lifecycle invariants for the MDRAP engine.

### Fundamental Invariant
> **MDRAP must never delete the only recoverable representation of committed state, nor silently construct a partial state upon corruption.**

### Mandatory Invariants
1. **Durable Snapshot Serialization:** Every snapshot must be atomically published and verified with a SHA-256 cryptographic digest over its canonical state serialization.
2. **Multi-Candidate Retention:** The storage layer retains up to `max_snapshots_retained` (default: 5) historical verified snapshots.
3. **Safe Segment Pruning:** WAL segments may only be deleted if all offsets in that segment are strictly less than the lowest retained recovery checkpoint:
   $$\text{prune\_cutoff} \le \min(\text{active\_snapshot\_offset}, \text{retained\_snapshot\_offsets}) + 1$$
4. **Fallback Recovery Order:**
   - Scan candidates ordered descending by applied offset.
   - For each candidate: load JSON $\to$ recompute SHA-256 digest $\to$ verify against stored `sha256`.
   - Verify that backing WAL contains contiguous history from $\text{snapshot\_offset} + 1$ up to current WAL head:
     $$\text{log.earliest\_offset} \le \text{snapshot\_offset} + 1$$
   - If corrupted or history is missing, skip all candidates at that offset and try the next oldest candidate.
5. **Fail-Closed on Truncation:** If all candidate snapshots fail verification (or no snapshot exists) and the WAL does not start at offset 0 (`log.earliest_offset > 0`), the engine **must raise `RecoveryError`** immediately. It must never initialize empty state or claim recovery success.

---

## 2. Snapshot Lifecycle & State Layout

### 2.1 File Naming & Manifest Conventions
- Versioned Snapshot: `snapshot_{offset:016d}.json` (e.g., `snapshot_0000000000002999.json`)
- Legacy Link: `snapshot.json` (atomically updated for backwards compatibility)
- Snapshot Manifest: `snapshot_manifest.json`

### 2.2 Manifest Schema
```json
{
  "version": 1,
  "latest_offset": 2999,
  "latest_snapshot": "snapshot_0000000000002999.json",
  "snapshots": [
    {"offset": 2999, "path": "snapshot_0000000000002999.json"},
    {"offset": 1999, "path": "snapshot_0000000000001999.json"},
    {"offset": 999, "path": "snapshot_0000000000000999.json"}
  ]
}
```

### 2.3 Atomic Write Sequence
1. Prepare `snap_data` with `"version": 2`, `"offset": last_applied_offset`, `"state": state.to_dict()`.
2. Compute `sha256` of canonical JSON bytes (`sort_keys=True, separators=(",", ":")`).
3. Write to temporary file: `snapshot_{offset}.tmp.{pid}_{timestamp_ns}`.
4. Flush stdio buffer and execute `os.fsync(f.fileno())`.
5. Atomic rename: `os.replace(tmp_path, versioned_path)`.
6. Atomic publication to `snapshot.json` and `snapshot_manifest.json`.
7. Retain up to `max_snapshots_retained` files; prune older snapshots only after new manifest is durable.

---

## 3. Recovery State Machine

```
              [Startup / Reopen Engine]
                         |
                         v
              [Discover Snapshot Candidates]
                         |
           +-------------+-------------+
           |                           |
     (Candidates Found)         (No Candidates)
           |                           |
           v                           |
    [Load Candidate N (Desc)]          |
           |                           |
           v                           |
    [SHA-256 Valid?]                   |
     /          \                      |
   (No)         (Yes)                  |
   /              \                    |
  v                v                   |
[Try N-1]   [WAL Has Range?]           |
     |         /        \              |
     |       (No)       (Yes)          |
     |       /            \            |
     +<-----+              v           |
     |              [Restore State N]  |
     |                     |           |
     |                     v           |
     |             [Replay From N+1]   |
     |                     |           |
     v                     |           v
[All Failed?]              |   [Check log.earliest_offset]
     |                     |           |
    (Yes)                  |           |
     +-------------------->+           |
                           |           |
                           v           v
                 [log.earliest_offset == 0?]
                          /        \
                        (Yes)      (No)
                        /            \
                       v              v
               [Replay From 0]   [RAISE RecoveryError]
                       |              (FAIL CLOSED)
                       v
               [Recovery Complete]
```

---

## 4. Recovery Telemetry (`RecoveryMetrics`)

Upon completion of startup, `engine.recovery_metrics` exposes:
- `snapshot_used` (`bool`): Whether state was seeded from a verified snapshot.
- `snapshot_offset` (`int`): Offset of the snapshot restored (-1 if full WAL replay).
- `replayed_records` (`int`): Count of records processed during catch-up from WAL.
- `valid_records` (`int`): Valid events accepted during replay.
- `quarantined_records` (`int`): Anomalous / corrupted events quarantined during replay.
- `corrupted_frames` (`int`): Torn tail frames safely detected and truncated.
- `recovery_duration_ms` (`float`): Total wall-clock time elapsed in recovery.
