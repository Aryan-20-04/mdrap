# MDRAP v5.3 — Durability Guarantees & Verification Matrix

## Overview
This document specifies the formal durability guarantees provided by MDRAP v5.3 under various operating system failures, hardware power losses, and process crash boundaries.

---

## 1. Formal Durability Contract

### 1.1 Invariant D1: Atomic Commit Boundary
An event is considered durable if and only if its WAL record has been flushed to persistent storage and acknowledged by the operating system kernel via `fsync()`.
- Under `fsync_policy="always"`: Durability is synchronous on each `step()`.
- Under `fsync_policy="grouped_by_time"`: Durability is asynchronous with bounded maximum latency window $\Delta t = 10\text{ ms}$.

### 1.2 Invariant D2: Bit-Identical Snapshot Reconstruction
Replaying WAL log frames from snapshot offset $S_k$ up to offset $S_{k+n}$ must produce an `EngineState` that is bit-identical to the state produced by live streaming processing.
Verified in `tests/test_wal_snapshot_replay_consistency.py`.

### 1.3 Invariant D3: Monotonic Sequence Numbering
Every committed market event possesses a strictly monotonically increasing sequence number per `(source, instrument)` tuple. Gaps and retransmissions are explicitly flagged with `SEQUENCE_GAP` and `DUPLICATE` reasons.

---

## 2. Failure Mode Matrix

| Failure Mode | Impact on MDRAP | Recovery Action | Verified Status |
| :--- | :--- | :--- | :--- |
| **Python Process Crash (SIGSEGV/SIGABRT)** | In-flight event in RAM lost | Engine restarts; reads WAL & snapshots | **VERIFIED** |
| **Operating System Panic (BSOD/Kernel Panic)** | Dirty OS page cache lost | On reboot, WAL truncated to last synced byte | **VERIFIED** |
| **Complete Power Failure** | NVMe disk write buffer flushed via battery/capacitor | CRC32 check discards torn tail | **VERIFIED** |
| **Disk Full (ENOSPC)** | WAL write fails | Engine raises `StorageFullError`; enters read-only pause | **VERIFIED** |
| **Snapshot Bit-Rot** | Corrupted snapshot file | Fallback to prior snapshot + replay | **VERIFIED** |
