# MDRAP v5.3 — Recovery & Fault Remediation Matrix

## Overview
This matrix summarizes platform recovery behaviors across hardware, network, process, data, and exchange feed faults.

---

## 1. System-Wide Recovery Matrix

| Fault Category | Specific Failure Event | Detection Mechanism | System Action & Containment | Post-Fault State |
| :--- | :--- | :--- | :--- | :--- |
| **Network Feeds** | WebSocket TCP Reset | Async heartbeat timeout | Immediate reconnect; sequence resync | Clean reconnect |
| **Network Feeds** | Packet Drops / Gaps | Monotonic sequence tracker | Quarantine gaps; request snapshot replay | Resynced |
| **Data Quality** | Crossed Books ($B \ge A$) | `fastpath.c` bitmask rule | Immediate status=`INVALID`; route to quarantine | Discarded |
| **Data Quality** | Clock Skew ($T_{ex} > T_{rec} + 1s$) | Timestamp comparator | Flagged `SUSPICIOUS` (`TS_IMPLAUSIBLE`) | Quarantined |
| **IPC Ring Buffer** | Slow Consumer Overrun | Seqlock write cursor check | Consumer logs `OVERRUN_DISCONTINUITY` | Catch-up |
| **IPC Ring Buffer** | Publisher Process Killed | Missing heartbeat in header | Consumers detect dead publisher; log warning | Disconnected |
| **Storage / WAL** | Process Killed (SIGKILL) | Startup lock / WAL scan | Scan WAL, replay from latest valid snapshot | Reconstructed |
| **Storage / WAL** | Partial Trailing Frame | CRC32 header/payload check | Truncate partial bytes; resume from boundary | Consistent |
| **Trading EMS** | Duplicate Order ID | `RealisticMatchingEngine` | Reject with `DUPLICATE_ORDER_ID` | Safe Reject |
| **Trading EMS** | Market Flash Crash | 14 Pre-Trade Risk Gates | Engage kill switch; cancel all open orders | Neutral Book |
