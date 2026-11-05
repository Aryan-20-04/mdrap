# MDRAP v5.0 — Concurrency & Memory Model Reassessment

**Audit Date**: 2026-11-05  
**Auditor**: Independent Principal Systems Engineer & Concurrency Specialist  
**Target Codebase**: `src/mdrap/partition.py`, `src/mdrap/shm.py`, `src/mdrap/fastpath.c`  
**Classification**: `[CONCURRENCY-AUDIT]`

---

## 1. Executive Summary

This report delivers an in-depth audit of the concurrent primitives, memory-ordering semantics, and lock-free data structures in MDRAP v5.0.

### Concurrency Verdict: **CONDITIONAL (Safe under CPython with GIL; Native SHM v3 is Lock-Free SPMC; Python SPSC requires native memory fences for free-threaded Python)**

---

## 2. Concurrency Mechanism Classification

| Subsystem Component | Implementation Location | Concurrency Classification | Memory Barriers & Primitives | Thread / Process Safety |
|:---|:---|:---:|:---|:---|
| **`SpscRingQueue`** | `src/mdrap/partition.py:35` | **Wait-Free (GIL-Dependent)** | Implicit CPython GIL sequential consistency; no explicit hardware memory barriers. | Single-Producer Single-Consumer ONLY. Unsafe on free-threaded Python (nogil). |
| **`SHM v3 Ring Buffer`** | `src/mdrap/fastpath.c:1471` | **Lock-Free (Producer & Consumer)** | C11 atomics: `__atomic_store_n` (RELEASE), `__atomic_load_n` (ACQUIRE), `__atomic_thread_fence`. | Single-Producer Multi-Consumer (SPMC). Multi-process safe. Concurrent writers prohibited. |
| **`Engine._lock`** | `src/mdrap/engine.py:488` | **Lock-Based (Mutex)** | Python `threading.RLock()` guarding state transitions, snapshot creation, and WAL rotation. | Multi-threaded safe for concurrent `submit()` calls. |
| **`ProjectionTracker`** | `src/mdrap/projection.py:124` | **Lock-Based (Mutex)** | Python `threading.Lock()` guarding `pending_batches` dequeue and checkpoint advancement. | Safe for concurrent async projection workers. |
| **`IngestLog._lock`** | `src/mdrap/ingestlog.py:410` | **Lock-Based (Mutex + CV)** | Python `threading.Lock()` + `threading.Condition()` for group commit and fsync coordination. | Multi-threaded append safe; single OS file handle access. |

---

## 3. SPSC Queue Audit (`src/mdrap/partition.py`)

### 3.1 Design & Algorithm
`SpscRingQueue` is a circular ring buffer sized to the nearest power of two (`capacity = 2^k`), with index masking via `head & self.mask` and `tail & self.mask`:
```python
def offer(self, item: Any) -> bool:
    head = self._head
    tail = self._tail
    if (head - tail) >= self.capacity:
        return False
    self._buffer[head & self.mask] = item
    self._head = head + 1
    return True

def poll(self) -> Any | None:
    tail = self._tail
    head = self._head
    if tail == head:
        return None
    idx = tail & self.mask
    item = self._buffer[idx]
    self._buffer[idx] = None
    self._tail = tail + 1
    return item
```

### 3.2 Concurrency Analysis
1. **Ownership Invariant**:
   - The **producer thread** owns write access to `self._head` and reads `self._tail`.
   - The **consumer thread** owns write access to `self._tail` and reads `self._head`.
   - Neither thread ever mutates the other thread's pointer.
2. **CPython GIL Dependency (Finding F-01)**:
   - In standard CPython (with GIL), Python bytecodes for reading and writing integers (`self._head = head + 1`) execute atomically. The GIL acts as a global sequential barrier, preventing compiler or hardware instruction reordering across Python threads.
   - **Weakness under PEP 703 (Free-Threaded Python 3.13t)**: In Python 3.13 builds configured with `--disable-gil`, Python bytecode execution does not enforce acquire-release memory fences. On weakly-ordered CPU architectures (such as ARM64), the CPU or compiler could reorder `self._head = head + 1` before `self._buffer[head & self.mask] = item`. The consumer could observe an updated `head` and read an unwritten or stale buffer entry (`None`).
   - **Remediation**: In v5.1, `SpscRingQueue` should be supplemented with a native C extension (`_fastpath_spsc.pyd`) utilizing explicit `__atomic_store_n(head, RELEASE)` and `__atomic_load_n(head, ACQUIRE)`.

---

## 4. Shared Memory v3 Lock-Free SPMC Audit (`src/mdrap/fastpath.c`)

### 4.1 Two-Phase Commit Protocol
SHM v3 implements an institutional two-phase commit protocol for 128-byte cache-aligned tick slots:
```c
/* Producer Publication Path (src/mdrap/fastpath.c:1497) */
MD_STORE_RLX_U64(slot, MD_UNCOMMITTED);                          /* 1. Invalidate slot */
MD_FENCE_RELEASE();                                              /*    Memory fence    */
for (int w = 1; w < 16; ++w) MD_STORE_RLX_U64(slot + w*8, src[w]); /* 2. Write 120-byte payload */
MD_STORE_REL_U64(slot, seq);                                     /* 3. Commit sequence (happens-before) */
MD_STORE_REL_U64(buf + SHM3_OFF_HEAD, seq + 1u);                 /* 4. Publish new head pointer */
```

### 4.2 Reader Seqlock & Torn-Read Elimination
```c
/* Consumer Read Path (src/mdrap/fastpath.c:1512) */
for (int retry = 0; retry < 3; ++retry) {
    const uint64_t c1 = MD_LOAD_ACQ_U64(slot);
    if (c1 != target_seq) {
        if (c1 == MD_UNCOMMITTED || c1 < target_seq) return 0;  /* In-flight or unwritten */
        return -1;                                              /* Lapped / overwritten */
    }
    uint64_t words[16];
    words[0] = c1;
    for (int w = 1; w < 16; ++w) words[w] = MD_LOAD_RLX_U64(slot + w*8);
    MD_FENCE_ACQUIRE();
    const uint64_t c2 = MD_LOAD_RLX_U64(slot);                   /* Re-read sequence after copy */
    if (c2 == c1) {
        memcpy(out, words, sizeof words);
        return 1;                                               /* Clean consistent read! */
    }
    MD_CPU_RELAX();
}
return -1;                                                      /* Lapped during retry */
```

### 4.3 Key Verification Properties
1. **Torn-Read Protection**: If a reader lands mid-write while the producer is updating the 120-byte payload, `c1` will be `MD_UNCOMMITTED` (reader returns 0) or `c2 != c1` (reader detects mismatch and retries). A torn read is never validated.
2. **Overrun / Lap Detection**: If a consumer falls behind by more than `slot_count` entries, `head - target_seq > slot_count`, returning `-1` (`SHM_OVERRUN`). The reader can safely resynchronize to the latest `head`.
3. **SPMC Invariant**: If multiple producers attempt to write to the same SHM buffer, the check `seq != head` returns `-2` (`CONTIGUITY_VIOLATION`). The SHM channel is strictly Single-Producer Multi-Consumer.
4. **Publisher Dead Detection**: Heartbeat timestamps in the SHM header are monitored by readers; if the publisher ceases updates for $> 500\text{ ms}$, readers enter `DETACHED` state.

---

## 5. False Sharing & Memory Alignment

- **Cache-Line Alignment**:
  - `ShmSlotV3` is exactly 128 bytes (`_Static_assert(sizeof(ShmSlotV3) == 128)`).
  - Modern x86-64 processors feature 64-byte L1 cache lines (and 128-byte dual-cache-line prefetchers). Each SHM slot spans exactly two 64-byte cache lines, preventing writes to slot $k$ from invalidating cache lines for slot $k+1$.
  - Verified in `src/mdrap/fastpath.c:1448`.

---

## 6. Concurrency Audit Sign-Off

The concurrency architecture is verified sound and robust under documented operational parameters: standard CPython 3.13 with GIL, single-producer per SHM channel, and lock-free seqlock read semantics.
