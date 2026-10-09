# MDRAP Phase 6 — Resource Governance & Bounded Capacity Specification

## 1. Executive Summary & Design Invariants
Unbounded memory growth and uncontrolled resource allocation are common causes of production outages in streaming platforms. MDRAP Phase 6 enforces strict, static mathematical ceilings on all queue depths, memory structures, file handles, and OS threads across every deployed shard.

---

## 2. Resource Ceiling Matrix (Per-Shard and Fleet Aggregates)

| Resource Dimension | Per-Shard Static Ceiling | 2-Shard Fleet Total | 4-Shard Fleet Total | Enforcement Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Ingress Queue Depth** | 50,000 events (~12.5 MB) | 100,000 events (25 MB) | 200,000 events (50 MB) | `queue.Queue(maxsize=50000)` |
| **Per-Client Fan-Out Queue** | 5,000 frames (~1.2 MB) | 10 clients = 12 MB | 25 clients = 30 MB | `queue.Queue(maxsize=5000)` |
| **SQLite Page Cache** | 64 MB (`cache_size=-65536`)| 128 MB | 256 MB | Database PRAGMA limit |
| **Welford Anomaly State** | 500 symbols $\times$ 24B = 12KB | 24 KB | 48 KB | Fixed struct allocation |
| **Max Open File Descriptors**| 32 FDs | 64 FDs | 128 FDs | Context manager lifecycle |
| **Total Resident Memory (RSS)**| **$\le$ 150.0 MB** | **$\le$ 300.0 MB** | **$\le$ 500.0 MB** | OS RSS memory bounds |

---

## 3. Failure Behavior Under Resource Saturation

1. **Ingress Queue Saturation**: If a shard's `in_queue` reaches 50,000 items, `enqueue_event()` returns `False`. The ingress router logs backpressure warning and temporarily throttles incoming feed reads rather than allocating unbounded memory.
2. **Consumer Buffer Saturation**: If a downstream consumer fails to drain its 5,000-frame queue, frames are dropped for that client and the client is automatically evicted after 10 drops.
3. **Storage Low Space**: If partition free space drops below 10%, the persistence engine triggers emergency segment compaction and notifies Alertmanager.
