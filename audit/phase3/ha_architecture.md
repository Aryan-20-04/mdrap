# MDRAP Phase 3 — High Availability Architecture

**Document Identifier**: `MDRAP-HA-P3-001`  
**Date**: October 9, 2026  
**Status**: APPROVED & IMPLEMENTED  

---

## 1. High Availability Topology

MDRAP implements an Active-Passive high-availability cluster topology:

```
          [Market Feeds]
                 │
        ┌────────┴────────┐
        ▼                 ▼
 ┌─────────────┐   ┌─────────────┐
 │ PRIMARY     │   │ STANDBY     │
 │ Node (A)    │──►│ Node (B)    │
 │             │HB │             │
 └─────────────┘   └─────────────┘
   WAL: Authoritative  WAL: Syncing replica
   Fencing Token: N    Standby Token: N
```

---

## 2. Finite State Machine Transitions

Nodes transition through 5 deterministic states:
- `PRIMARY`: Authoritative writer. Emits periodic heartbeats containing cluster `epoch`, `fencing_token`, and `last_committed_seq`.
- `STANDBY`: Passive follower. Monitors primary heartbeat liveness. Replicates WAL frames.
- `SYNCING`: Catch-up state entered when a failover condition is met but local sequence is behind the primary watermark.
- `FAILED`: Fatal local error state (e.g. disk failure or poisoned WAL).
- `MAINTENANCE`: Operator-initiated administrative drain.
