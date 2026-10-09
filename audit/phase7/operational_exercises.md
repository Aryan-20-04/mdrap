# MDRAP Phase 7 — Operational Incident Exercises & GameDay Runbooks

## 1. Executive Summary & Objective
To ensure site reliability engineers (SRE) and trading operations desks can respond deterministically to live system incidents, MDRAP establishes standardized **Operational Incident Exercises (GameDays)**.

All exercises are executed in isolated staging environments using synthetic replay feeds without impacting production infrastructure.

---

## 2. Standardized Incident Exercises

### Exercise 1: Live Primary Shard Crash & Standby Takeover
- **Preconditions**: Staging cluster running 2 active shards under 15,000 eps traffic; warm standby configured for Shard 0.
- **Injected Fault**: `kill -9` issued to the primary Shard 0 process.
- **Expected Alerts**:
  - `ShardHeartbeatMissing` fired within 3.0s.
  - `ShardFencingPromoted` emitted by standby.
- **System Behavior**: Shard 1 continues processing symbols `M-Z` without interruption. Standby Shard 0 acquires `shard.lock`, increments `epoch.seq`, replays the local WAL segment, and opens client ports.
- **Operator Actions**: Verify Prometheus dashboard shows Shard 0 status `HEALTHY` and consumers successfully reconnected.
- **Success Criteria**: Recovery time objective (RTO) $\le 2.0\text{ seconds}$; zero dropped events on Shard 1; sequence monotonicity preserved.

### Exercise 2: Slow Consumer Denial-of-Service Defense
- **Preconditions**: Shard processing 20,000 eps with 10 connected algorithmic consumers.
- **Injected Fault**: Synthetic consumer deliberately pauses TCP socket reads.
- **Expected Alerts**:
  - `ConsumerQueueBackpressure` emitted for client ID.
  - `ConsumerEvicted` triggered after 10 drops.
- **System Behavior**: Normal consumers continue receiving wire-speed ticks ($< 20\text{ \mu s}$ latency). Slow consumer socket is closed; memory is reclaimed.
- **Operator Actions**: None required (automatic containment). Review audit log for evicted client ID.
- **Success Criteria**: Engine throughput remains $\ge 20,000\text{ eps}$; memory RSS delta $\le 1.0\text{ MB}$; 9 healthy consumers experience zero dropped ticks.

### Exercise 3: Storage Disk Saturation Mitigation
- **Preconditions**: SQLite storage database approaching 90% disk utilization.
- **Injected Fault**: Disk fill injection via simulated large temp file.
- **Expected Alerts**: `DiskSpaceLow` warning at 85% threshold; `DiskSpaceCritical` alert at 95%.
- **System Behavior**: Shard prioritizes hot memory IPC and IngestLog WAL; pauses non-essential analytical projections.
- **Operator Actions**: Trigger EOD archival compaction script (`python -m mdrap.archive --compress zstd`).
- **Success Criteria**: 82.8% disk space reclaimed in $< 30\text{ seconds}$; zero interruption to live tick streams.
