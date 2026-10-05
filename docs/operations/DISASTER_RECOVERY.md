# MDRAP Institutional Disaster Recovery Plan

## 1. Overview & Positioning

The **Market Data Reliability & Acceleration Platform (MDRAP)** provides high-throughput normalization, validation, reconciliation, and provenance tracking for institutional market data feeds.

In accordance with institutional requirements (Spec §23, §25, §26), MDRAP deploys an Active-Passive high-availability topology ensuring continuous availability, zero tick data loss, and sub-second failover recovery.

---

## 2. Recovery Objectives (SLAs)

| Metric | Target | Description |
|---|---|---|
| **RTO (Recovery Time Objective)** | **< 1.0 second** | Maximum allowable downtime during an unplanned primary process or node failure before standby takeover. |
| **RPO (Recovery Point Objective)** | **0 ticks (RPO = 0)** | Zero tick data loss guaranteed through dual-feed ingestion, append-only binary journal replication, and sequence catch-up verification. |
| **Replay Determinism** | **100% Identical** | Bit-for-bit, tick-for-tick identical quality classifications and canonical decisions under historical replay. |
| **Data Integrity Verification** | **Continuous** | Tamper-evident Merkle-chained audit trails and periodic root hash anchoring. |

---

## 3. Architecture & High Availability Topology

```text
               +-------------------------------------------+
               |         Upstream Market Feeds             |
               | (NASDAQ ITCH, SBE, Direct Exchange TCP/WS)|
               +---------------------+---------------------+
                                     |
                +--------------------+--------------------+
                |                                         |
                v                                         v
   +--------------------------+              +--------------------------+
   |      PRIMARY NODE        |              |       STANDBY NODE       |
   | - Feed Ingestion         |  Heartbeat   | - Passive Ingestion      |
   | - Quality Engine         |<------------>| - Journal Catch-Up       |
   | - SHM Ring Buffer Pub    |  (Epoch Fenc)| - Standby Watchdog       |
   | - Binary Journal Writer  |              | - Ready for Promotion    |
   +------------+-------------+              +------------+-------------+
                |                                         |
                +--------------------+--------------------+
                                     |
                                     v
                 +---------------------------------------+
                 |       Shared Non-Volatile Storage     |
                 | - Append-Only Binary Journals (.dbn)  |
                 | - SQLite WAL Database                 |
                 | - Partitioned Historical Store        |
                 +---------------------------------------+
```

### Key Components

1. **Epoch-Based Fencing Tokens**:
   - Every node operates under an epoch counter (`epoch` / `fencing_token`).
   - Promotions strictly increment the epoch. Any former primary process attempting to broadcast heartbeats or journal writes with a stale epoch is immediately fenced and rejected.

2. **Split-Brain Mitigation**:
   - If two nodes concurrently assert `PRIMARY` in the same epoch, deterministic tie-breaking rules enforce that the node with the lower lexicographical `node_id` maintains primary authority while the other yields to `STANDBY`.

3. **Sequence Catch-Up Guarantee**:
   - Before a standby node asserts `PRIMARY`, it validates its local journal sequence against the last reported primary sequence. If lagging, it enters `SYNCING` state to drain contiguous backlog before completing promotion.

---

## 4. Incident Severity Classification

| Severity | Definition | Target Response | Target Resolution |
|---|---|---|---|
| **SEV-1 (Critical)** | Primary node crash, complete feed blackout across all venues, or detected data corruption. | < 2 minutes | < 5 minutes (Auto-failover: < 1s) |
| **SEV-2 (High)** | Single venue feed dropped, degraded throughput, secondary standby unreachable, or storage backpressure. | < 10 minutes | < 30 minutes |
| **SEV-3 (Medium)** | Non-blocking anomaly alerts, minor clock drift (>100µs), elevated rate limiter evictions. | < 30 minutes | < 2 hours |
| **SEV-4 (Low)** | Routine maintenance, historical partition archival, metric label cleanup. | Next business day | Scheduled window |

---

## 5. Recovery Procedures

### Scenario A: Unplanned Primary Crash
1. Standby node detects missed primary heartbeats exceeding `heartbeat_timeout_s` (default 2.0s).
2. Standby verifies sequence catch-up parity.
3. Standby executes `promote()`, incrementing cluster epoch and assuming primary responsibilities.
4. Alerts are dispatched to PagerDuty/Slack via `MDRAP_ALERT_WEBHOOK`.

### Scenario B: Database or Disk Failure
1. If SQLite reports `sqlite3.OperationalError` (disk full / I/O error), MDRAP immediately redirects writes to in-memory fallback ring buffers and write-ahead binary journal.
2. Operator switches storage backend to disaster recovery mount or triggers partition flushing.

### Scenario C: Split-Brain Network Partition
1. Partition heals. Both nodes detect competing primary heartbeats.
2. Deterministic node ID tie-breaking forces the junior node to yield to `STANDBY`.
3. High-epoch fencing token ensures downstream consumers discard writes from demoted node.
