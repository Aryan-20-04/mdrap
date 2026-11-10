# Native C, SIMD & Concurrency — Recommendations

## Priority 0 (Release Blockers)
1. **Protect Pipeline Batches with Lock (F-NAT-01)**:
   - Introduce `self._batch_lock = threading.Lock()` in `Pipeline` protecting `_canonical_batch`, `_quarantine_batch`, `_lineage_batch`.
2. **Add MSVC Atomics to `_fastpath_c.c` (F-NAT-02)**:
   - Add `#if defined(_MSC_VER)` shims using `_InterlockedExchange64` and `_InterlockedCompareExchange64`.
3. **Fix Windows Headers in `mdrap_core.c` (F-NAT-03)**:
   - Guard `<x86intrin.h>` behind `#if !defined(_MSC_VER)` and use `<intrin.h>` with `__cpuid` on MSVC.

## Priority 1 (Concurrency & IPC)
4. **Publish Head Sequence Per-Tick (F-NAT-04)**:
   - In `mdrap_core.c`, publish head sequence on every tick or after microsecond timeout.
5. **Thread-Safe Replay Ring (F-NAT-05)**:
   - Protect `replay_ring` writes and reads with `MD_LOCK(&eng->lock)` or seqlock.
6. **Check `PyErr_Occurred()` in C Extension (F-NAT-06)**:
   - Ensure clean error handling before returning PyObjects.
7. **Thread-Local Scratch Arrays in `FastQualityEngine` (F-NAT-07)**:
   - Use `threading.local()` for ctypes scratch buffers or lock `evaluate_batch()`.\n