# MDRAP Phase 8 — Asynchronous Backpressure Design

## 1. Backpressure Strategy & Bounded Buffers
In financial market data systems, slow consumers cannot push back pressure onto live exchanges without risking catastrophic data loss or exchange disconnects. MDRAP implements **downstream backpressure shedding with rigorous accounting**:

1. **Producer Handoff**: The publisher enqueues events to `_incoming_queue` (capacity: 50,000 frames). If the background dispatcher cannot keep pace with extreme bursts, incoming buffer saturation sheds the oldest incoming frame while logging drop metrics.
2. **Consumer Desynchronization**: Each consumer has an isolated queue with an immutable upper bound (`max_buffer_per_client = 1,000`). If a consumer thread or socket stalls, its private queue overflows and drops the oldest unread frames (`queue.popleft()`).
3. **No Lock Contention**: The publisher never acquires locks on individual client queues. The dispatch loop acquires individual client locks only during the batch fan-out phase.

## 2. Memory Bounds Calculation
For a fleet supporting 100 concurrent consumers:
$$\text{Max Client Buffer Memory} = 100 \times 1,000 \times 256\text{ bytes} \approx 25.6\text{ MB}$$
$$\text{Max Incoming Buffer Memory} = 50,000 \times 256\text{ bytes} \approx 12.8\text{ MB}$$
$$\text{Total Maximum Fan-Out Heap Footprint} \le 40\text{ MB}$$
Memory usage remains bounded and deterministic regardless of stream duration or client behavior.
