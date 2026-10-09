# MDRAP Phase 6 — Capacity Automation & Scaling Governance Policy

## 1. Executive Summary & Policy Statement
In cloud-native web applications, dynamic autoscaling (e.g. Kubernetes Horizontal Pod Autoscaler adding and killing pods based on CPU load) is standard. In mission-critical financial market data systems, however, **uncontrolled dynamic autoscaling is a severe hazard**.

Killing an active market data shard mid-session causes catastrophic sequence gaps, in-flight transaction loss, and exchange sequence desynchronization. Therefore, MDRAP Phase 6 enforces a **Predictive, Scheduled Capacity Scaling Model** rather than unconstrained reactive autoscaling.

---

## 2. Dynamic Autoscaling Hazards in Market Data

1. **Sequence Domain Rupture**: Terminating a shard to "scale down" severs the active monotonic sequence domain, forcing downstream trading algorithms to halt or enter emergency recovery.
2. **Rebalancing Latency Spikes**: Re-allocating symbols between shards while trades are flowing induces queue reordering and temporary state freezes.
3. **Flapping / Oscillation**: Market data volume naturally spikes 10x during market open (09:30 EST) and immediately subsides 3 minutes later. Reactive autoscaling causes thrashing (starting pods that become ready just as the burst ends).

---

## 3. The MDRAP Capacity Governance Policy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MDRAP CAPACITY GOVERNANCE MODEL                       │
├───────────────────────────────┬─────────────────────────────────────────────┤
│ Operational Action            │ Policy Rule & Automation Standard           │
├───────────────────────────────┼─────────────────────────────────────────────┤
│ **Dynamic Scale-Down**        │ **STRICTLY PROHIBITED** during market hours │
│ **Dynamic In-Session Scale-Up**│ Operator-supervised via pre-warmed standbys │
│ **Scheduled Horizon Scaling** │ Automated EOD rebalancing based on volume   │
│ **Burst Cushioning**          │ IngestLog ring buffers absorb 50k surges   │
└───────────────────────────────┴─────────────────────────────────────────────┘
```

### Scheduled Horizon Scaling Protocol
- **Trigger**: If peak queue utilization exceeds 60% over 3 consecutive trading sessions, capacity automation flags the need for shard expansion.
- **Execution Window**: Executed strictly between 17:00 EST and 08:00 EST during scheduled maintenance windows.
- **Process**: SRE updates `num_shards` from $K$ to $K+2$, executes partition recalculation, runs preflight verification, and brings up new shards prior to market open.
