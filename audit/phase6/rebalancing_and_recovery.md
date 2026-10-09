# MDRAP Phase 6 — Partition Rebalancing, Migration & Crash Recovery

## 1. Executive Summary & Policy Invariant
Dynamic partition rebalancing during live market hours introduces severe hazards: dropped packets, sequence discontinuities, and temporary pipeline stalls that disrupt trading algorithms.

MDRAP Phase 6 enforces **Static Trading Session Boundaries with Cold EOD Rebalancing**, eliminating all runtime rebalancing jitter.

---

## 2. Cold EOD Rebalancing Protocol

When universe growth or volume skew requires adding shards or adjusting symbol ranges, rebalancing executes during the post-market maintenance window (17:00 – 08:00 EST):

```
[ Step 1: Halt Ingress ] ──> [ Step 2: Flush & Checkpoint WAL ] ──> [ Step 3: Update Config Manifest ]
                                                                                   │
[ Step 6: Resume Consumers ] <── [ Step 5: Verify Sequence Heads ] <── [ Step 4: Restart Shards ]
```

### Detailed Execution Steps:
1. **Halt Ingress**: Stop incoming feed listeners; allow in-flight events to drain through shard queues.
2. **Checkpoint & Flush**: Execute `PRAGMA wal_checkpoint(TRUNCATE)` on all shard SQLite databases; close active `.seg` files.
3. **Update Manifest**: Modify `shard_ranges` or `num_shards` in `config/fleet_production.json`.
4. **Restart Shards**: Launch updated shard instances; verify lock acquisition and health status.
5. **Consumer Sync**: Consumers reconnect and re-read the partition routing table.

---

## 3. Shard Crash Recovery Protocol

If an individual shard process terminates unexpectedly:
1. **Zero Impact on Peer Shards**: Other shards continue operating without pause.
2. **Autonomous Recovery**: The recovering shard restarts, opens its exclusive lock (`shard.lock`), and scans its active `.seg` WAL segment.
3. **CRC32 Replay**: Uncheckpointed records are verified against CRC32 checksums and replayed into memory.
4. **Sequence Continuity**: The shard resumes emitting sequence numbers starting exactly from `last_seq + 1`.
