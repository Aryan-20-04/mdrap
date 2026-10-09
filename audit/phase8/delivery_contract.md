# MDRAP Phase 8 — Consumer Delivery Contract

## 1. Delivery Semantics & Guarantees
MDRAP's stream fan-out layer provides the following operational delivery contract:

| Property | Guarantee Level | Architectural Enforcement |
|---|---|---|
| **Ordering** | Strictly FIFO per Symbol/Partition | Enforced by monotonic sequence numbers and sequential deque dispatch. |
| **Delivery Mode** | At-Most-Once under Backpressure | Clients that consume faster than incoming market rate receive Exactly-Once delivery. Stalled clients drop oldest unread ticks to maintain real-time timeliness. |
| **Sequence Gaps** | Observable & Recoverable | If frames are dropped due to client-side buffer saturation, the sequence numbers reflect the gap, allowing client-driven replay from `ReplayBuffer`. |
| **Producer Isolation** | Zero Head-of-Line Blocking | Producer never blocks on client TCP socket or consumer deque saturation. |
| **Drop Accounting** | Zero Silent Drops (INV-01) | Every dropped frame increments `frames_dropped` on the client session and platform `total_dropped`. |

## 2. Overload & Shedding Policy
When a client fails to read frames fast enough to keep pace with the market:
1. **Watermark Threshold**: Bounded client queue (`max_queue_size`, default 1,000 frames).
2. **Eviction Policy**: If `frames_dropped >= 50`, the client is flagged as a noisy neighbor and automatically evicted.
3. **Audit Recording**: Evictions are recorded in telemetry (`total_evictions`) and logged at WARNING level with tenant ID.
