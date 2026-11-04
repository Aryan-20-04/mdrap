# MDRAP v5.0 — Current Architecture & Subsystem Reconnaissance

**Document ID:** `docs/v5/architecture/CURRENT_ARCHITECTURE.md`  
**Classification:** Architectural Analysis & Codebase Reconnaissance  
**Auditor / Architect:** Principal Low-Latency Systems Architect & Combined Engineering Team  
**Evidence Standard:** Direct Source Code Inspection (`src/mdrap/`) & Test Suite Verification  

---

## 1. System Identity & Source Baseline

- **Repository Path:** `c:\Users\KIIT0001\Desktop\Projects\mdrap`
- **Git Branch:** `v5.0-research-baseline`
- **Baseline Git Commit:** `1919caa` (Direct parent: `b88c3b3`, Ancestor: `3ab9566`)
- **Version Identifier:** `4.0.1` (`src/mdrap/_version.py`)
- **Runtime Environment:** Windows 11 Build 26200 AMD64, Python 3.13.1 (MSC v.1942 64-bit)
- **CPU Architecture:** Intel Core i7-1255U (Family 6 Model 154 Stepping 4): 2 P-cores (4 threads) + 8 E-cores (8 threads) = 10 physical cores / 12 logical threads.
- **Native Compilers Available:** GCC 14.2.0 (MSYS2 mingw-w64), MSVC 19.42 (`cl.exe`).
- **Native ABI Version:** ABI Version 5 (`fastpath_engine_abi_version() == 5`).

---

## 2. Comprehensive Subsystem Reconnaissance

Every core module in `src/mdrap/` was inspected across seven critical engineering dimensions:

```
+----------------------------------------------------------------------------------------------------+
|                                    MDRAP SUBSYSTEM MAP                                             |
+----------------------------------------------------------------------------------------------------+
|  INGRESS & ADAPTERS  |  CORE ENGINE & PIPELINE   |  NATIVE ACCELERATION   |  PERSISTENCE & PROJ    |
|  - ws_feed.py        |  - engine.py (Engine)     |  - fastpath.c (Native) |  - ingestlog.py (WAL)  |
|  - simulator.py      |  - pipeline.py (Pipeline) |  - _fastpath_c.c (API) |  - snapshot.py (Snap)  |
|  - itch.py / sbe.py  |  - quality.py (Python)    |  - mdrap_core.c (Daemon)| - projection.py (DB)  |
|  - cme.py / eurex.py |  - reconciliation.py      |  - shm.py (Shared Mem) |  - bardb.py / depth.py |
+----------------------------------------------------------------------------------------------------+
```

### Detailed Subsystem Audit Matrix

| Subsystem / Module | Primary Responsibility | Instantiation Site | Caller(s) | On Hot Path? | Independent State? | Duplicates Another? | Error Handling & Test Coverage |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| **`Engine` (`engine.py`)** | Unified stateful pipeline, WAL persistence, undo journaling, snapshot cadence. | CLI, Service, Benchmarks | User code, Service, Pipeline (when `use_engine=True`) | **YES** | Yes (`EngineState`) | Partial overlap with `Pipeline` | Comprehensive try/except with rollback journal; 100% test coverage. |
| **`Pipeline` (`pipeline.py`)** | Modular legacy pipeline (`ingest` $\rightarrow$ `normalize` $\rightarrow$ `quality` $\rightarrow$ `reconcile` $\rightarrow$ `storage`). | CLI, API, Service | API, Service, Integration tests | **CONDITIONAL** | Yes (Modular components) | Duplicates `Engine` state updates | Exceptions propagate to caller; fully tested in Gate 6. |
| **`FastQualityEngine` (`fastpath.py`)** | C FFI wrapper invoking vectorized C kernel for 7 quality rules. | `Pipeline`, `FastQualityEngine()` | `Pipeline`, API, Service, Benchmarks | **YES** | Yes (C heap `FastEngine`) | Duplicates `QualityEngine` (pure Python) | Catches FFI errors with graceful fallback to Python; fully tested. |
| **`fastpath.c`** | Pure C native hot-path kernel (Welford variance, 64-bit sequence bitmask, 32-entry circular ring). | Native DLL / shared library | `_fastpath_c.c`, `fastpath.py`, `mdrap_core.c` | **YES** | Yes (`FastSlot` array in C) | Reference implementation for `QualityEngine` | Compile-time static assertions (`_Static_assert`); 100% parity tests. |
| **`_fastpath_c.c`** | CPython C-API extension eliminating ctypes FFI boxing. | Python import (`_fastpath_c`) | `fastpath.py` | **YES** | Stateless FFI bridge | Replaces `ctypes.CDLL` calls | Direct CPython exception raising (`PyErr_Occurred()`); tested. |
| **`IngestLog` (`ingestlog.py`)** | Sequential binary Write-Ahead Log with CRC-32 framing and group commit. | `Engine.open()` | `Engine.submit()`, recovery routines | **YES** | Yes (Segment files, index files) | None (Authoritative recovery source) | Atomic rollback on I/O failure, segment repair; heavily tested. |
| **`SnapshotManager` (`snapshot.py`)** | Multi-generation state snapshotting with atomic rename and segment pruning. | `Engine.open()` | `Engine.submit()` (cadence), CLI | **BACKGROUND** | Yes (JSON state files) | None | Fail-closed multi-generation fallback; heavily tested. |
| **`SQLiteProjection` (`projection.py`)** | Relational projection of canonical events and quarantine records. | User code, `EngineConfig` | `Engine.submit()` via `ProjectionTracker` | **YES (Disk)** | Yes (`.db` schema) | Overlaps with `DuckDBProjection` | Retry on lock/busy, fallback to queue; heavily tested in P0. |
| **`SHMWriter` / `SHMReader` (`shm.py`)** | Lock-free SPMC shared memory ring buffer with Seqlock two-phase commit. | CLI, Service, Native Daemon | Strategy SDK, co-located consumers | **HOT (IPC)** | Yes (POSIX/Win32 shared memory) | None | Reader lap detection and telemetry; tested in Gate 6. |
| **`mdrap_core.c`** | Standalone native C daemon executing zero-Python critical path to SHM. | CLI / binary execution | Standalone deployment | **HOT (Daemon)**| Yes (Standalone C engine) | Duplicates Python `Engine` pipeline | Native signal handling, invariant TSC clock; tested in Gate 12. |

---

## 3. Critical Structural Findings & Dual-Path Duplication

### 3.1 The Dual-Semantic Path Problem (`Engine` vs. `Pipeline`)
`[SOURCE-VERIFIED]` In MDRAP v4.0.1, two distinct architectural pipeline models exist concurrently:
1. **The `Engine` Path (`src/mdrap/engine.py`)**:
   - Monolithic, stateful, transactional engine.
   - Wraps every submission in a `Transaction(state)` rollback journal.
   - Evaluates quality in pure Python (`_step_internal`).
   - Appends to `IngestLog` (WAL) and manages SQLite projections directly.
2. **The `Pipeline` Path (`src/mdrap/pipeline.py`)**:
   - Modular functional pipeline.
   - Uses `FastQualityEngine` (native C hot path kernel via `fastpath.c`).
   - Employs modular components (`gateway.ingest`, `gateway.normalize`, `reconciler`).
   - If `self.use_engine=True`, it delegates internally to `Engine.submit()`.

**Architectural Consequence**:
When `use_engine=True`, the platform routes through `Engine.submit()`, bypassing native C quality acceleration in favor of pure Python `_step_internal`! This explains why in-memory engine benchmarks run at ~77.8K EPS rather than utilizing the 40M+ ticks/sec native C kernel.

---

## 4. Evidence Classification Summary

- `[SOURCE-VERIFIED]` Direct inspection of `src/mdrap/engine.py`, `pipeline.py`, `fastpath.py`, `fastpath.c`, `ingestlog.py`, `projection.py`, `shm.py`.
- `[TEST-EXECUTED]` Release Gates 1–14 execution logs (`audit/v4.0.1-final-certification/raw_evidence/all_release_gates.log`).
