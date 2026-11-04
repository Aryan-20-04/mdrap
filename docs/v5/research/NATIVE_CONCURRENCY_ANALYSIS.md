# MDRAP v5.0 — Native Concurrency & C11 Memory Model Analysis

**Document ID:** `docs/v5/research/NATIVE_CONCURRENCY_ANALYSIS.md`  
**Classification:** Research Analysis & Concurrency Engineering  
**Auditor / Architect:** Principal Low-Latency Systems Architect & Native Concurrency Specialist  
**Evidence Standard:** C11 Standard ISO/IEC 9899:2011 & Hardware Memory Architecture  

---

## 1. Primary Reference & Standards Context

- **Source Reference:** C11 Standard (`<stdatomic.h>`), cppreference:
  - URL: https://cppreference.com/w/c/atomic/memory_order
- **Primary Problem Solved:** Provides formal, portable memory ordering guarantees across modern multi-core processors (x86 Total Store Order vs. ARM Weakly-Ordered), eliminating data races, stale register reads, and out-of-order store visibility without coarse-grained mutex locking.

---

## 2. Memory Order Semantics & Hardware Mapping

Modern multi-core processors aggressively reorder instructions and buffer stores in hardware store buffers. C11 defines explicit ordering semantics:

```
+-----------------------------------------------------------------------------------------+
|                                C11 MEMORY ORDERING SPECTRUM                             |
+-----------------------------------------------------------------------------------------+
|  Relaxed           |  Acquire / Release              |  Sequentially Consistent         |
|  memory_order_relaxed | memory_order_acquire / release  |  memory_order_seq_cst            |
|  - Atomic value read/write | - Release: stores prior to write | - Total global store order   |
|  - No fence / ordering  |   are visible to Acquire load   | - Heavy hardware bus lock    |
|  - Monotonic counters   | - Plain MOV on x86 TSO (no fence) | - High latency penalty       |
+-----------------------------------------------------------------------------------------+
```

### 2.1 The Critical Fallacy: `volatile` is NOT Atomic
- `[EXTERNAL-RESEARCH]` In C and C++, the `volatile` keyword guarantees only that the compiler will not optimize away a read or write; it instructs the compiler to read from memory on every access.
- **What `volatile` DOES NOT Do**:
  - It emits **no CPU memory barriers** (`mfence`, `dmb`).
  - It does **not prevent hardware CPU reordering**.
  - It does **not guarantee atomicity** on multi-word structures.
  - It does **not ensure visibility** across CPU cores on weakly-ordered architectures (ARM64).
- **MDRAP Rule `[SOURCE-VERIFIED]`**: All inter-thread and inter-process synchronization in MDRAP must use explicit atomic operations (`__atomic_*` built-ins or C11 `stdatomic.h`), never bare `volatile`.

### 2.2 Acquire-Release Semantics in SPSC & SPMC Rings
- `[EXTERNAL-RESEARCH]` The gold standard for zero-lock message passing is **Acquire-Release ordering**:
  - **Producer (Publishing)**:
    1. Write message payload to slot memory using relaxed stores: `buf[offset] = data`.
    2. Commit sequence using release store: `__atomic_store_n(&slot->commit_seq, seq, __ATOMIC_RELEASE)`.
    3. Hardware guarantee: All payload writes are flushed to cache coherence before the sequence update becomes visible.
  - **Consumer (Reading)**:
    1. Read sequence using acquire load: `seq = __atomic_load_n(&slot->commit_seq, __ATOMIC_ACQUIRE)`.
    2. Hardware guarantee: Subsequent payload reads cannot be reordered before the sequence check.
- **x86-64 Architecture Advantage**:
  - On x86-64, the processor hardware enforces Total Store Order (TSO). Every store is implicitly a release store, and every load is implicitly an acquire load. Acquire-release operations compile to standard `MOV` instructions with zero CPU cycle penalty!

---

## 3. The Lock-Free Seqlock Pattern (Single-Writer Multi-Reader)

In `src/mdrap/shm.py` and `src/mdrap/mdrap_core.c`, MDRAP implements a cacheline-aligned Seqlock protocol for zero-copy IPC:

```c
// WRITER:
uint64_t seq = head++;
slot->commit_seq = UNCOMMITTED;     // 1. Mark slot uncommitted (odd/busy)
__atomic_thread_fence(__ATOMIC_RELEASE);

// 2. Write 128-byte payload in-place
memcpy(&slot->payload, &tick, sizeof(tick));

// 3. Atomic commit
__atomic_store_n(&slot->commit_seq, seq, __ATOMIC_RELEASE);

// READER:
uint64_t seq1 = __atomic_load_n(&slot->commit_seq, __ATOMIC_ACQUIRE);
if (seq1 == UNCOMMITTED || seq1 != target_seq) return MISSED;

// Read payload
memcpy(&local_tick, &slot->payload, sizeof(local_tick));

uint64_t seq2 = __atomic_load_n(&slot->commit_seq, __ATOMIC_ACQUIRE);
if (seq1 != seq2) return TORN_READ_RETRY;   // Slot was overwritten mid-read
```

### Seqlock Properties
1. **Readers never block the writer**: The market data feed writer progresses at full wire speed regardless of reader crashes or slowdowns.
2. **Torn reads are detected**: If a slow reader is interrupted mid-copy, the post-copy sequence check detects the change and retries.
3. **No Dynamic Memory Allocation**: Operates entirely in fixed, pre-allocated shared memory.

---

## 4. Cacheline False Sharing & Padding Rules

- `[EXTERNAL-RESEARCH]` False sharing occurs when independent variables accessed by different CPU cores reside on the same 64-byte line.
- In MDRAP native C:
  - Slot structures are padded to exact multiples of 64 bytes (`_Static_assert(sizeof(FastSlot) == 384)`).
  - SHM headers isolate the writer hot-line (64 bytes) from the diagnostics line (64 bytes).
  - Intra-process queues isolate producer tail pointers from consumer head pointers with 64-byte padding:
    ```c
    typedef struct {
        alignas(64) uint64_t head;
        uint8_t _pad1[56];
        alignas(64) uint64_t tail;
        uint8_t _pad2[56];
    } SpscQueue;
    ```

---

## 5. Architectural Verdict for MDRAP v5.0

1. **Eliminate Python Mutex Locks on Hot Path `[SOURCE-VERIFIED]`**:
   - Replace Python `threading.Lock` in `Engine.submit()` with SPSC lock-free queues using C11 acquire-release atomics.
2. **Preserve Seqlock SPMC Shared Memory `[SOURCE-VERIFIED]`**:
   - The verified 128-byte Seqlock SHM implementation in `fastpath.c` and `shm.py` is sound and must be retained as the low-latency inter-process distribution layer.

---

## 6. Evidence Classification Summary

- `[EXTERNAL-RESEARCH]` ISO/IEC 9899:2011 (C11) `<stdatomic.h>` specification.
- `[SOURCE-VERIFIED]` `src/mdrap/fastpath.c` (atomic built-in macros), `src/mdrap/mdrap_core.c` (seqlock SHM implementation).
