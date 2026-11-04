# MDRAP v5 Concurrency Model: Lock-Free Threading & Core Topology

## 1. Executive Summary

This document specifies the concurrency and synchronization architecture for MDRAP v5.0.

In financial market data systems, traditional lock-based concurrency (mutexes, condition variables, read-write locks) introduces unbounded tail latency spikes, priority inversions, and thread context switching costs ($\sim 1.2\text{--}4.5\ \mu\text{s}$ per switch). Furthermore, on hybrid CPU architectures (e.g., the host Intel Core i7-1255U with 2 P-Cores and 8 E-Cores), un-pinned threads suffer severe latency jitter when migrated across heterogeneous execution cores.

MDRAP v5 adopts a **Shared-Nothing, Lock-Free Concurrency Architecture**:
1. **Single-Writer Hotpath**: One dedicated Performance Core (P-Core) executes the entire validation and logging pipeline for a given feed channel without acquiring locks or yielding the CPU.
2. **Lock-Free SPSC Queues**: Thread-to-thread communication (e.g., Hotpath to SQLite Projector) uses pre-allocated Single Producer Single Consumer ring buffers with C11 atomic acquire/release semantics.
3. **Seqlock IPC**: Inter-process distribution to external strategy engines uses a lock-free Sequence Lock (Seqlock) over memory-mapped files.
4. **CPython GIL Elimination**: Hotpath execution is 100% native C; the CPython Global Interpreter Lock is never acquired on the data plane.

---

## 2. Thread Topology & Hybrid CPU Core Affinity

The baseline development environment is an **Intel Core i7-1255U** processor:
- **2 Performance Cores (P-Cores)**: Logical Processors 0, 1, 2, 3 (Hyper-Threaded, high IPC, base 1.7 GHz / boost 4.7 GHz, 12 MB shared L3 cache).
- **8 Efficiency Cores (E-Cores)**: Logical Processors 4, 5, 6, 7, 8, 9, 10, 11 (Single-threaded, throughput-oriented, base 1.2 GHz / boost 3.5 GHz).

```
+-----------------------------------------------------------------------------------------+
|                               Intel Core i7-1255U CPU                                   |
+---------------------------------------------------+-------------------------------------+
|         Performance Cores (Golden Cove)           |      Efficiency Cores (Gracemont)   |
|         Logical Cores 0, 1, 2, 3                  |      Logical Cores 4, 5, 6, 7,      |
|                                                   |                    8, 9, 10, 11     |
+---------------------------------------------------+-------------------------------------+
| Core 0 (Thread 0): Wire Ingress & Quality Hotpath | Core 4: Async SQLite Projector      |
| Core 0 (Thread 1): Binary WAL Disk Flush Worker   | Core 5: SQLite Checkpoint & Storage |
| Core 1 (Thread 2): Retransmit & Gap Request Agent | Core 6-7: Python Telemetry & Server |
| Core 1 (Thread 3): Reserved / Spare P-Core        | Core 8-11: OS & Background Tasks    |
+---------------------------------------------------+-------------------------------------+
```

### Affinity Configuration Implementation
- **Windows**: `SetThreadAffinityMask(GetCurrentThread(), (1ULL << core_id));`
- **Linux**: `pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset);`
- **Verification**: `GetProcessAffinityMask()` / `sched_getaffinity()`.

---

## 3. Lock-Free Single Producer Single Consumer (SPSC) Ring Buffer

Inter-thread messaging between the Native Hotpath and the Asynchronous SQLite Projector uses a cache-line isolated SPSC ring buffer.

### Cache-Line Padding & False Sharing Prevention
To eliminate CPU cache-line bouncing (MESI protocol invalidations between Core 0 and Core 4), the `head` (producer write index) and `tail` (consumer read index) reside on distinct 64-byte cache lines.

```c
typedef struct {
    // Producer cache line (64 bytes)
    _Alignas(64) _Atomic uint64_t head;       // Write index updated by producer
    uint64_t mask;                           // Capacity - 1 (power of two)
    uint8_t  _pad1[64 - sizeof(_Atomic uint64_t) - sizeof(uint64_t)];

    // Consumer cache line (64 bytes)
    _Alignas(64) _Atomic uint64_t tail;       // Read index updated by consumer
    uint8_t  _pad2[64 - sizeof(_Atomic uint64_t)];

    // Shared buffer pointer (64 bytes)
    _Alignas(64) NativeSlot* slots;           // Pre-allocated slot array
    uint64_t capacity;                       // Capacity (must be power of two)
} SpscRing;
```

### Memory Ordering Semantics (C11 Atomics)

#### Producer Enqueue (Hotpath Thread):
```c
bool spsc_enqueue(SpscRing* ring, const NativeSlot* slot) {
    uint64_t head = atomic_load_explicit(&ring->head, memory_order_relaxed);
    uint64_t tail = atomic_load_explicit(&ring->tail, memory_order_acquire);

    // Check if buffer is full
    if ((head - tail) >= ring->capacity) {
        return false; // Queue full - handle backpressure/drop count
    }

    // Copy slot into pre-allocated memory
    ring->slots[head & ring->mask] = *slot;

    // Release fence: ensure slot data is globally visible before updating head
    atomic_store_explicit(&ring->head, head + 1, memory_order_release);
    return true;
}
```

#### Consumer Dequeue (Projection Thread):
```c
bool spsc_dequeue(SpscRing* ring, NativeSlot* out_slot) {
    uint64_t tail = atomic_load_explicit(&ring->tail, memory_order_relaxed);
    uint64_t head = atomic_load_explicit(&ring->head, memory_order_acquire);

    // Check if buffer is empty
    if (tail == head) {
        return false; // Queue empty
    }

    // Read slot from memory
    *out_slot = ring->slots[tail & ring->mask];

    // Release fence: ensure read is complete before advancing tail
    atomic_store_explicit(&ring->tail, tail + 1, memory_order_release);
    return true;
}
```

---

## 4. Seqlock Protocol for Shared Memory Ring Buffer (SPMC)

For IPC distribution to multiple external strategy processes, MDRAP v5 employs a **Sequence Lock (Seqlock)**. This guarantees lock-free, zero-copy reads for multiple consumers while allowing the single writer to publish without ever waiting for slow readers.

### Writer Protocol:
1. Increment `sequence` to an **odd** number using `memory_order_release` (signals write in progress).
2. Write `NativeSlot` payload into mapped memory page.
3. Increment `sequence` to the next **even** number using `memory_order_release` (signals write complete).

```c
void shm_publish(ShmRing* ring, const NativeSlot* slot) {
    uint64_t idx = atomic_fetch_add_explicit(&ring->producer_idx, 1, memory_order_relaxed);
    ShmSlot* target = &ring->slots[idx & ring->mask];

    // Step 1: Start write transaction (odd sequence)
    uint64_t seq = atomic_load_explicit(&target->sequence, memory_order_relaxed);
    atomic_store_explicit(&target->sequence, seq + 1, memory_order_release);

    // Step 2: Copy payload
    memcpy(&target->slot, slot, sizeof(NativeSlot));

    // Step 3: Complete write transaction (even sequence)
    atomic_store_explicit(&target->sequence, seq + 2, memory_order_release);
}
```

### Reader Protocol:
1. Read `sequence` with `memory_order_acquire`. If odd, spin or yield (write in progress).
2. Copy `NativeSlot` payload into local buffer.
3. Read `sequence` again with `memory_order_acquire`.
4. If `sequence_before != sequence_after`, the read was torn by an interleaved write; discard and retry.
5. If `producer_idx - read_idx > capacity`, reader was lapped (overrun); resynchronize from snapshot.

---

## 5. Elimination of GIL Contention

In MDRAP v4.0.1, Python threads running the event loop contend for the Global Interpreter Lock, introducing 50–150 $\mu\text{s}$ tail latency spikes.

In MDRAP v5:
- The data plane ingress, validation, and SHM publishing loop runs in pure C without calling the Python C-API.
- If Python scripts interact with the engine, they do so via:
  1. Reading shared atomic telemetry counters via memory-mapped memory blocks (`mmap`).
  2. Batched FFI calls where the GIL is released:
     ```python
     # Python out-of-path batch consumer
     lib.mdrap_drain_batch_no_gil(ctypes.byref(batch_buf), 1000)
     ```
  3. Administrative socket IPC (Unix domain socket or Named Pipe) for control messages.
