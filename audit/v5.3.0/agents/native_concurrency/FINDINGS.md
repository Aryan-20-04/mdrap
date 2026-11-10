# Native C, SIMD & Concurrency — Detailed Findings

## Executive Summary
Audited C source files (`fastpath.c`, `mdrap_core.c`, `_fastpath_c.c`), build system (`build_fastpath.py`), shared memory IPC (`shm.py`), and pipeline concurrency. The native acceleration layer features high-performance cache-line aligned seqlocks and SPSC circular queues. However, 3 CRITICAL defects were identified: multithreaded race conditions in pipeline batching, and MSVC build breakages in `_fastpath_c.c` and `mdrap_core.c`.

## Categorized Findings

### CRITICAL
1. **F-NAT-01: Multithreaded Race Condition Between Background Writer and Tick Ingestion**:
   - `src/mdrap/pipeline.py:388-396` and `1147-1158`: Background writer thread `_writer_loop` calls `flush()` which checks and reassigns `self._canonical_batch, self._quarantine_batch, self._lineage_batch = [], [], []`.
   - Main thread simultaneously calls `_enqueue_canonical()` appending to `self._canonical_batch` without any lock.
   - **Impact**: Lost events, torn batch state, list resizing race conditions under real load.

2. **F-NAT-02: MSVC Build Breakage in C-API Extension (`_fastpath_c.c`)**:
   - `src/_fastpath_c.c:515-574`: Directly invokes GCC/Clang built-ins `__atomic_load_n` and `__atomic_store_n`.
   - Missing MSVC atomic shims.
   - **Impact**: Fails compilation on Windows with MSVC (`error C3861: '__atomic_load_n': identifier not found`).

3. **F-NAT-03: MSVC Build Breakage in Standalone Daemon (`mdrap_core.c`)**:
   - `src/mdrap/mdrap_core.c:102-107, 122-124`: Includes `<x86intrin.h>` and `<cpuid.h>` which do not exist in MSVC runtime.
   - **Impact**: Cannot compile `mdrap-core.exe` on Windows using MSVC.

### HIGH
4. **F-NAT-04: Unbounded IPC Latency — Head Sequence Published Only Every 32 Ticks**:
   - `src/mdrap/mdrap_core.c:487-493`: Only updates SHM head sequence when `(seq & 0x1F) == 0`.
   - **Impact**: Low-frequency tickers or quiet feeds hold back up to 31 ticks of events from downstream readers.

5. **F-NAT-05: Data Race & Torn Reads in Native Replay Buffer**:
   - `src/mdrap/fastpath.c:1019-1080`: `fastpath_engine_replay_record` and `fastpath_engine_replay_slice` omit locks and seqlocks.
   - **Impact**: Concurrent reads observe torn struct fields and data races.

6. **F-NAT-06: Fatal CPython SystemError — Returning PyObject With Pending Exception**:
   - `src/_fastpath_c.c:232-255, 348-373`: Fails to check `PyErr_Occurred()` after argument conversions and returns objects with exceptions set.

7. **F-NAT-07: Concurrent Scratch Buffer Corruption in `evaluate_batch()`**:
   - `src/mdrap/fastpath.py:1351-1358`: `_batch_c_events` and `_batch_c_results` are shared instance variables. When `thread_safe=False` (default), concurrent callers corrupt each other's memory.

8. **F-NAT-08: Catastrophic Floating-Point Cancellation in MSVC Variance Fallback**:
   - `src/mdrap/fastpath.c:871-880`: On platforms lacking `__int128`, falls back to naive $\frac{\sum X^2}{N} - (\bar{X})^2$ in 64-bit float, losing all precision on large prices.

### MEDIUM
9. **F-NAT-09: Misleading Claim of SIMD SoA Evaluation**:
   - `src/mdrap/fastpath.c:883-968`: Advertised as SIMD batch evaluation, but executes an unvectorized scalar loop.
10. **F-NAT-10: Illegal Instruction (SIGILL) Risk — Missing Runtime CPUID Check**:
    - `src/mdrap/mdrap_core.c:458`: Executes AVX2 instructions based solely on `#ifdef __AVX2__` without runtime CPUID validation.\n