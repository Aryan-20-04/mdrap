# MDRAP 6.0.0 — Native C Memory Safety, Buffer Bounds & Spinlock Audit

**Audit Date:** November 2026  
**Auditor:** Senior C/C++ Concurrency Specialist & Systems Auditor  
**Target:** `src/mdrap/fastpath.c`, `src/mdrap/mdrap_core.c`, `src/mdrap/_fastpath_c.c`  

---

## 1. Executive Summary

This audit performs an exhaustive memory safety review of MDRAP's native C acceleration layer. The source code was inspected for dynamic allocation vulnerabilities (`malloc`/`free`), pointer aliasing, buffer overruns, integer overflows, alignment issues, and spinlock correctness.

---

## 2. Memory Management Architecture

The C hot path allocates internal ring buffers upon engine creation:

```c
FastEngine *fastpath_engine_create(double staleness_threshold_s, double anomaly_stddev, int32_t price_window) {
    FastEngine *e = (FastEngine *)calloc(1, sizeof(FastEngine));
    if (!e) return NULL;
    // Circular price windows for anomaly detection
    e->ring_buffers = (double *)calloc(MAX_INSTRUMENTS * MAX_WINDOW, sizeof(double));
    if (!e->ring_buffers) {
        free(e);
        return NULL;
    }
    ...
    return e;
}
```

### Allocation Audit Points
1. **`calloc` vs `malloc`:** `calloc` is systematically used, guaranteeing zero-initialized buffers and preventing uninitialized memory reads.
2. **Buffer Bounds Checks:** Instrument IDs and Source IDs are checked against `MAX_INSTRUMENTS` (1024) and `MAX_SOURCES` (64).
   ```c
   if (ev->instrument_id < 0 || ev->instrument_id >= MAX_INSTRUMENTS) return;
   if (ev->source_id < 0 || ev->source_id >= MAX_SOURCES) return;
   ```
   All array lookups are guarded against out-of-bounds index access.

---

## 3. Memory Safety Vulnerabilities Identified

### 1. Concurrency Lifetime Race (UAF) in `fastpath_evaluate()`
- **Severity:** **CRITICAL (P0 / P1)**
- **File & Line:** `src/mdrap/fastpath.c:1244`
- **Issue:** `fastpath_evaluate` obtains `get_default_engine()` without holding a reference or lock. `fastpath_cleanup()` can concurrently execute `fastpath_engine_destroy(old)`, freeing `e->ring_buffers` and `e`. Dereferencing `e` inside `fastpath_engine_evaluate` causes an **Invalid Memory Access / Segfault**.

### 2. Duplicate Native Source Code Files
- **Severity:** Medium (P2)
- **Files:**
  - `src/mdrap/fastpath.c` (Active kernel, 86 KB)
  - `src/mdrap/mdrap_core.c` (Wrapper that includes `fastpath.c`)
  - `src/mdrap/_fastpath_c.c` (Legacy C file, 12 KB)
  - `src/_fastpath_c.c` (Legacy root duplicate, 12 KB)
- **Issue:** Multiple legacy C source files remain in the repository. Compiling the wrong source file or updating one without the other creates severe build drift.

### 3. Spinlock Correctness & Memory Ordering
- **Spinlock Implementation:**
  ```c
  #if defined(_MSC_VER)
  #define MD_LOCK(l) while (_InterlockedCompareExchange((volatile long*)(l), 1, 0) != 0) { _mm_pause(); }
  #define MD_UNLOCK(l) _InterlockedExchange((volatile long*)(l), 0)
  #else
  #define MD_LOCK(l) while (__atomic_test_and_set((l), __ATOMIC_ACQUIRE)) { __builtin_ia32_pause(); }
  #define MD_UNLOCK(l) __atomic_clear((l), __ATOMIC_RELEASE)
  #endif
  ```
- **Analysis:**
  - On MSVC, `_InterlockedCompareExchange` and `_InterlockedExchange` enforce full memory barriers.
  - On GCC/Clang, `__ATOMIC_ACQUIRE` and `__ATOMIC_RELEASE` provide correct acquire/release semantics.
  - The spinlock implementation itself is correct, but **its scope of application is fatally flawed** because it was not wrapped around evaluation call sites!

---

## 4. Memory Safety Scorecard

| Safety Dimension | Status | Notes |
| :--- | :--- | :--- |
| **Buffer Overflow Protection** | **SOUND** | Guarded by `MAX_INSTRUMENTS` / `MAX_SOURCES` bounds checks |
| **Integer Overflow** | **SOUND** | Scaling using 64-bit signed integers |
| **Dynamic Allocations in Hot Path**| **ZERO** | Zero `malloc` calls during steady-state processing |
| **Memory Lifetime & Teardown** | **CRITICAL DEFECT** | **Use-After-Free race during concurrent cleanup** |
| **Code Redundancy** | **NEEDS CLEANUP** | 2 duplicate legacy C files present |
