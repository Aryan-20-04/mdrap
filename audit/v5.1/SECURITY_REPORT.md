# MDRAP v5.1 — Institutional Security & Memory Safety Audit Report

**Author**: Senior Reliability & Security Engineer  
**Branch**: `v5.1-development`  
**Classification**: `[SOURCE-VERIFIED, TEST-EXECUTED & CODEQL-AUDITED]`  
**Date**: 2026-11-05  

---

## 1. Memory Safety & Native C Bounds Verification

The native C accelerator kernel (`fastpath.c`, `_fastpath_c.c`, `mdrap_core.c`) implements static geometric bounding to eliminate memory corruption vulnerabilities:

### 1.1 Fixed Geometric Array Sizing
- `MAX_SOURCES`: 32 feeds.
- `MAX_INSTRUMENTS`: 8,192 instruments.
- Every source ID ($s$) and instrument ID ($i$) is bounds-checked at the FFI entry point:
  ```c
  if (s_id < 0 || s_id >= MAX_SOURCES || i_id < 0 || i_id >= MAX_INSTRUMENTS) {
      return (uint64_t)STATUS_INVALID << 32 | REASON_SCHEMA_VIOLATION;
  }
  ```
- **Zero Heap Allocations on Hot Path**: State for all 262,144 slot pairs is pre-allocated upon engine creation. No `malloc()` or `free()` calls occur during per-tick event evaluation.

### 1.2 Integer Overflow Protection
- Timestamps and sequence numbers are 64-bit integers (`int64_t` / `uint64_t`).
- Monotonic sequence checks reject jumps exceeding `max_jump_limit` (default 1,000,000) to prevent sequence number manipulation attacks.
- Price fixed-point math uses 128-bit integer intermediate registers (`__int128_t` in GCC) to prevent overflow during variance summation.

---

## 2. IPC Shared Memory (SHM) Isolation & Permissions

- **POSIX Permissions**: Set strictly to `0600` (`S_IRUSR | S_IWUSR`), restricting shared memory segment access solely to the running user ID.
- **Seqlock Reader Decoupling**: Readers only perform atomic loads on `head` and slot sequences. A reader process crash, hang, or infinite loop cannot corrupt the publisher ring buffer or block publisher progression.
- **Channel Isolation**: Each channel writer writes to an independent SHM segment, preventing cross-tenant memory poisoning.

---

## 3. Cryptographic Storage & Authentication

- **API Keys**: Stored as PBKDF2 hashes with per-key salt. Plaintext API keys never touch disk.
- **Timing Attack Resistance**: All token and signature comparisons use constant-time comparison (`hmac.compare_digest` in Python / constant-time memcmp in C).
- **Audit Lineage**: Rejection reasons and quarantine events are digitally logged with monotonic IDs, establishing an immutable audit trail for market surveillance.
