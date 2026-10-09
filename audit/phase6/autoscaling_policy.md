# MDRAP Phase 6 — Autoscaling Policy & Capacity Governance Rules

## 1. Executive Summary & Policy Statement
Dynamic in-session autoscaling introduces severe hazards in market data processing: sequence ruptures, connection flapping, and packet drops during burst regimes.

MDRAP Phase 6 formally establishes the **Scheduled Capacity Scaling Policy**:

$$\text{Dynamic In-Session Scale-Down} = \mathbf{PROHIBITED}$$
$$\text{Capacity Expansion} = \mathbf{SCHEDULED\text{ }EOD\text{ }REBALANCING}$$

---

## 2. Capacity Signals and Evaluation Windows

Capacity is monitored across three primary metrics over a rolling 3-trading-day observation window:

1. **Peak Queue Occupancy**: Maximum depth of `in_queue` during market open (09:30 EST).
   - *Threshold*: If peak occupancy exceeds 60% of capacity (30,000 / 50,000 slots) over 3 consecutive sessions $\rightarrow$ Shard expansion triggered.
2. **Persistence Queue Dwell Time**: Average time an event spends waiting for IngestLog WAL commit.
   - *Threshold*: If dwell time exceeds $250\text{ \mu s}$ sustained $\rightarrow$ Storage sharding expansion triggered.
3. **Consumer Eviction Frequency**: Number of slow consumers disconnected due to buffer exhaustion.
   - *Threshold*: If $> 3$ evictions occur per day on healthy desks $\rightarrow$ Consumer queue buffer size adjusted.

---

## 3. Scheduled Expansion Protocol
When an expansion threshold is breached:
1. Operations schedules shard expansion for the upcoming post-market maintenance window.
2. New shard definitions (e.g., expanding from 2 to 4 shards) are added to `config/fleet_production.json`.
3. Shards are pre-warmed, validated with `deploy_pilot.py --check-only`, and brought online prior to 08:00 EST.
