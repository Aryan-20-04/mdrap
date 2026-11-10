# Native C, SIMD & Concurrency — Exact Evidence

## 1. Pipeline Race Condition
- File: `src/mdrap/pipeline.py:388-396` (Writer Thread)
  ```python
  if (self._canonical_batch or self._quarantine_batch or self._lineage_batch) and ...:
      self.flush(wait=False)
  ```
- File: `src/mdrap/pipeline.py:432-440` (Ingest Thread)
  ```python
  def _enqueue_canonical(self, event: CanonicalEvent) -> None:
      self._canonical_batch.append(event)
  ```
- File: `src/mdrap/pipeline.py:1152-1158` (Flush)
  ```python
  canon, quar, lin = self._canonical_batch, self._quarantine_batch, self._lineage_batch
  self._canonical_batch, self._quarantine_batch, self._lineage_batch = [], [], []
  ```

## 2. Missing MSVC Atomics in `_fastpath_c.c`
- File: `src/_fastpath_c.c:515, 523`
  ```c
  tail = __atomic_load_n(&r->tail, __ATOMIC_ACQUIRE);
  __atomic_store_n(&r->head, head + 1, __ATOMIC_RELEASE);
  ```

## 3. Invalid MSVC Headers in `mdrap_core.c`
- File: `src/mdrap/mdrap_core.c:103-105`
  ```c
  #include <x86intrin.h>
  #include <immintrin.h>
  #include <cpuid.h>
  ```\n