# MDRAP v5.0 Native C Hotpath Memory Safety, Alignment & ABI Audit Findings

## 1. Scope & Verification Strategy

This audit inspects the memory safety, resource lifecycle, cacheline alignment, and ABI stability of the native C extensions:
- `src/mdrap/fastpath.c`
- `src/mdrap/fastpath.h`
- `src/mdrap/mdrap_core.c`
- `src/mdrap/_fastpath_c.c`

---

## 2. Memory Safety & Static Geometry Findings

### Finding MEM-01: Zero Dynamic Heap Allocation on Hot Path
- **Classification**: `[SOURCE-VERIFIED]`
- **Source Location**: `src/mdrap/fastpath.c:fastpath_evaluate_event`
- **Specification**: In quantitative low-latency architecture, `malloc()` and `free()` must never be called during tick evaluation, SBE decoding, or shared-memory writes. Dynamic heap allocation introduces unpredictable latency spikes due to heap metadata lock contention and memory fragmentation.
- **Finding**: Inspection of `fastpath_evaluate_event()`, `native_shm_write_tick()`, and `fastpath_crc32c()` confirms that **zero calls to `malloc()`, `calloc()`, or `realloc()` exist on the tick execution path**. All tables and slots are pre-allocated at startup.
- **Verdict**: **COMPLIANT**.

### Finding MEM-02: Bounded Static State Geometry
- **Classification**: `[SOURCE-VERIFIED]`
- **Source Location**: `src/mdrap/fastpath.h:35`
- **Geometry Contract**:
  ```c
  #define MAX_SOURCES 32
  #define MAX_INSTRUMENTS 8192
  #define TOTAL_SLOTS (MAX_SOURCES * MAX_INSTRUMENTS) // 262,144 slots
  ```
- **Finding**:
  - Memory footprint is statically capped at $262,144 \times 64 \text{ bytes} = 16.0 \text{ MB}$.
  - Indexing uses bitwise arithmetic: `slot_idx = (source_id << 13) | instrument_id`.
  - Bounds checks: `if (source_id >= MAX_SOURCES || instrument_id >= MAX_INSTRUMENTS) return STATUS_FALLBACK_PYTHON;`
  - Eliminates heap exhaustion, buffer overflow vulnerabilities, and unbounded state growth.
- **Verdict**: **COMPLIANT**.

### Finding MEM-03: 64-Byte Cache-Line Alignment & False Sharing
- **Classification**: `[SOURCE-VERIFIED]` `[BENCHMARK-MEASURED]`
- **Source Location**: `src/mdrap/fastpath.h:55`, `src/mdrap/shm.py:120`
- **Analysis**:
  - Structure `FastSlot` is explicitly decorated with `__attribute__((aligned(64)))`.
  - In `shm.py`, Cache Line 1 (Writer Hot Line: `head_seq`, `epoch_id`) and Cache Line 2 (Heartbeat & Diagnostics: `heartbeat_ts`, `watermark_flags`) are separated into distinct 64-byte padded segments.
  - Updating heartbeat or checking watermark warnings does not invalidate the CPU L1 cache line holding `head_seq`.
- **Verdict**: **COMPLIANT**.

### Finding MEM-04: Hardware CRC32C CPU Feature Detection & Fallback
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/fastpath.c:2364`, `src/mdrap/fastpath.py:2364`
- **Analysis**:
  - Native implementation uses `#if defined(__SSE4_2__) || defined(_MSC_VER)` to emit hardware CRC32C instructions (`_mm_crc32_u64`).
  - If compiled on non-x86 or non-SSE4.2 systems, it falls back to the Castagnoli polynomial table lookup (`0x82F63B78`).
  - Pure Python fallback in `fastpath.py` implements the identical Castagnoli polynomial bitwise loop.
- **Audit Execution**: Tested against the official RFC 3720 test vector (`b"123456789"` -> `0xE3069283`). Both native and pure-Python paths produce `0xE3069283`.
- **Verdict**: **COMPLIANT**.

### Finding MEM-05: C ABI Stability & Boundary Marshaling
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/fastpath.py:650`
- **Analysis**:
  - In `fastpath.py`, `_CFastEvent` and `_CFastResult` structures match the C struct memory layout with explicit `ctypes` field definitions (`c_double`, `c_uint64`, `c_char * 16`).
  - In Phase 1, `_ensure_batch_scratch()` was introduced to reuse pre-allocated ctypes arrays, eliminating array allocation churn per batch.
- **Verdict**: **COMPLIANT**.
