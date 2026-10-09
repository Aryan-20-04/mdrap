# MDRAP Phase 7 — Stateful & Model-Based Verification Strategy

## 1. Executive Summary & Objective
Isolated stateless unit tests cannot detect bugs arising from complex state transitions, uncoordinated concurrency, or recovery sequences.

**Model-Based Verification** introduces lightweight, mathematically defined reference state machines against which the real MDRAP runtime, partition ownership, and consumer lifecycle states are continuously asserted.

---

## 2. Reference State Models

### Model 1: Partition Lifecycle & Fencing State Machine

```
              ┌──────────────────┐
              │   UNCLAIMED      │
              └─────────┬────────┘
                        │ Acquire shard.lock
                        ▼
              ┌──────────────────┐
              │   FENCE_HELD     │
              └─────────┬────────┘
                        │ Verify CRC32 & Replay WAL
                        ▼
              ┌──────────────────┐
              │  ACTIVE_WRITER   │
              └────┬────────┬────┘
                   │        │
     Graceful Stop │        │ Fencing collision / Lock lost
                   ▼        ▼
       ┌──────────────┐   ┌───────────────────────────┐
       │   DRAINED    │   │ TERMINATED (Exit Code 42) │
       └──────────────┘   └───────────────────────────┘
```

#### Valid State Transitions:
1. `UNCLAIMED` $\rightarrow$ `FENCE_HELD`: Allowed only via successful OS file descriptor lock acquisition.
2. `FENCE_HELD` $\rightarrow$ `ACTIVE_WRITER`: Allowed only after WAL integrity check and epoch generation counter increment.
3. `ACTIVE_WRITER` $\rightarrow$ `DRAINED`: Initiated by `SIGTERM` or administrative CLI `fleet drain`.
4. `*` $\rightarrow$ `TERMINATED`: Immediate fail-closed transition if lock contention occurs.

#### Forbidden State Transitions:
- `UNCLAIMED` $\rightarrow$ `ACTIVE_WRITER`: Barred (must hold fence first).
- Multiple instances in `ACTIVE_WRITER` simultaneously for the same partition ID: Mathematically barred by OS kernel file locks.

---

### Model 2: Consumer Fan-Out & Eviction State Machine

```
              ┌──────────────────┐
              │    HEALTHY       │ (Queue occupancy < 80%)
              └─────────┬────────┘
                        │ Queue occupancy ≥ 80%
                        ▼
              ┌──────────────────┐
              │   CONGESTED      │ (Non-blocking enqueue, 0 drops)
              └─────────┬────────┘
                        │ Queue occupancy = 100% (Drops increment)
                        ▼
              ┌──────────────────┐
              │   BACKPRESSURE   │ (Drops < 10)
              └─────────┬────────┘
                        │ Drops ≥ 10
                        ▼
              ┌──────────────────┐
              │    EVICTED       │ (Socket closed, memory reclaimed)
              └──────────────────┘
```

#### Invariant Assertions:
- A client in `BACKPRESSURE` never blocks the engine thread.
- Transition to `EVICTED` occurs deterministically upon the 10th dropped event.
- An evicted client cannot cause memory leaks or stalled socket descriptors.
