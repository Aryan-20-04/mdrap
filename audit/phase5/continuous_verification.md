# MDRAP Phase 5 — Continuous Verification and Production Probing

## 1. Executive Summary & Verification Intent
In financial market infrastructure, static testing before deployment is insufficient; the platform must actively verify its own health, correctness contracts, and invariants while in operation. MDRAP implements **Continuous Production Verification (CPV)**—a suite of non-disruptive, automated probes running continuously alongside live traffic to detect latent data corruption, memory leaks, or sequence anomalies before they affect consumers.

---

## 2. Continuous In-Band and Out-of-Band Probes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CONTINUOUS VERIFICATION ENGINE                        │
└───────┬─────────────────┬─────────────────┬─────────────────┬───────────────┘
        ▼                 ▼                 ▼                 ▼
 ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 │ Liveness &   │  │ Synthetic    │  │ Storage & DB │  │ Cryptographic│
 │ Heartbeats   │  │ Canary Trade │  │ Integrity    │  │ Merkle Check │
 │ (Every 5 sec)│  │ (Every 60 sec│  │ (Every 1 hr) │  │ (Every EOD)  │
 └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

### Probe 1: Sub-Second Heartbeat & Sequence Continuity Probe
- **Frequency**: Every 5.0 seconds.
- **Mechanism**: The watchdog thread issues an internal synthetic heartbeat frame (`0x00`) into the SBE distribution pipeline.
- **Verification**: Verifies that reader queues are draining, TCP sockets are receptive, and monotonic sequence numbers advance without stalling.
- **Failure Trigger**: If heartbeat round-trip exceeds 100 ms, alert `MDRAPHeartbeatStallWarning` is raised.

### Probe 2: Synthetic Canary Ingestion Probe
- **Frequency**: Every 60 seconds during off-peak periods / continuous in pilot sandbox.
- **Mechanism**: Injects a marked synthetic trade event for symbol `CANARY.TEST` with known mathematical price and volume.
- **Verification**: Verifies that the entire pipeline correctly ingests, timestamps, normalizes, applies quality rules, logs to WAL, and emits the SBE frame with exact numerical fidelity.
- **Isolation**: Downstream production consumers discard the `CANARY.TEST` symbol via venue/symbol whitelist.

### Probe 3: Storage and Database Integrity Background Probe
- **Frequency**: Every 1 hour.
- **Mechanism**: Dedicated read-only thread executes SQLite PRAGMAs:
  ```sql
  PRAGMA quick_check;
  PRAGMA foreign_key_check;
  ```
- **Verification**: Confirms zero B-tree corruption, zero page corruption, and clean WAL state without taking write locks.

### Probe 4: Merkle Hash Chain and Replay Parity Probe
- **Frequency**: Daily at market close.
- **Mechanism**: Reads 1,000 randomly sampled events from `events.seg` WAL, re-computes their canonical hashes, and compares against the recorded Merkle tree root in `merkle_audit`.
- **Guarantee**: Confirms tamper-evident immutability of historical market records.
