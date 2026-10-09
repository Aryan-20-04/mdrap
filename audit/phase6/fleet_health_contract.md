# MDRAP Phase 6 — Fleet Health State Contract & Aggregation Semantics

## 1. Executive Summary & Purpose
This contract establishes the formal health state semantics for multi-shard MDRAP deployments. In distributed systems, high-level dashboards often present misleading "green" aggregate statuses while individual critical partitions suffer outages. This specification guarantees that partition-level failures are deterministically elevated to the fleet level.

---

## 2. Formal Health State Machine

```
      ┌─────────────┐
      │   HEALTHY   │ (All shards operational, queue depth < 20%, sequences monotonic)
      └──────┬──────┘
             │ (Any shard queue > 50%, 1 shard stopped, or slow consumer evictions)
             ▼
      ┌─────────────┐
      │  DEGRADED   │ (Traffic partially flowing; at least one shard impaired)
      └──────┬──────┘
             │ (Storage exhaustion < 10%, WAL write failure, or >= 50% shards down)
             ▼
      ┌─────────────┐
      │  CRITICAL   │ (Emergency halt; automated failover / PagerDuty P1 engage)
      └─────────────┘
```

---

## 3. Strict Aggregation Invariants

1. **Non-Concealment Invariant**:
   $$\text{FleetStatus} = \min_{i \in \{0, \dots, K-1\}} \left( \text{ShardStatus}_{i} \right)$$
   If Shard 0 is `HEALTHY` and Shard 1 is `DEGRADED`, the overall fleet status **MUST BE DEGRADED**. Under no circumstance may the fleet indicate `HEALTHY` when any individual shard is impaired.
2. **Sequence Monotonicity Progression Check**:
   A shard is classified as `STALLED` if incoming feed events are queued but `sequence_head` fails to advance for $> 500\text{ ms}$.
3. **Queue Saturation Boundaries**:
   - `queue_depth / max_capacity < 0.20`: `HEALTHY`
   - `0.20 <= queue_depth / max_capacity < 0.80`: `WARNING` (Elevates shard to `DEGRADED`)
   - `queue_depth / max_capacity >= 0.80`: `CRITICAL`
