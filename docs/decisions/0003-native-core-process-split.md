# Architecture Decision Record: Native Core Process Split and Language Strategy

- **Date**: 2026-09-22
- **Status**: Accepted
- **Scope**: Core Hot Path · Process Boundary · Language Strategy (Phases 17 & 18)

---

## 1. Context & Problem Statement

In MDRAP v2.1 (T0 tier), even when the C accelerator (`fastpath.c`) is active, each market data tick still traverses a CPython interpreter frame via `ctypes`. While the pure C validation logic executes in ~24–37 ns, the Python-to-C FFI boundary adds ~350 ns to 1.7 µs of overhead. Furthermore, the single-process model forces garbage collection pauses, GIL contention, and downstream storage/display operations onto the same process space as the live feed ingest.

To reach the **T1 tier (~1–10 µs wire-to-decision)**, the system must achieve **zero Python interpreter frames on the per-event critical path**.

---

## 2. Decision: Process Split via Lock-Free Shared Memory Ring

We decouple the system into two distinct OS processes:

```
┌─────────────────────────┐   Lock-Free SPSC Ring Buffer   ┌──────────────────────────┐
│  mdrap-core (Native)    │ ─────────────────────────────▶ │   mdrap (Python)         │
│  - Line-rate ingest     │     (Shared Memory: /dev/shm)  │   - CLI & Terminal Cockpit│
│  - QualityEngine (7-rule)│                                │   - SQLite WAL / DuckDB  │
│  - Lock-free SPSC commit│ ◀───────────────────────────── │   - Downstream TCA/Alpha │
└─────────────────────────┘      Control Channel (Signals)  └──────────────────────────┘
```

1. **`mdrap-core` (Hot Path)**:
   - Standalone native binary.
   - Owns feed ingestion, timestamping, 7-rule quality scoring, and SPSC ring buffer commits.
   - Operates with zero mutex locks on the per-event path (`fastpath_engine_evaluate_unlocked`).
2. **`mdrap` (Control Plane & Consumers)**:
   - Runs off the hot path as an asynchronous consumer draining the shared memory ring buffer.
   - Retains rich Python ergonomics for reporting, backtesting, SQLite persistence, and UI display.

---

## 3. Language Strategy: Retain C with Process Split Now, Backlog Rust

Following the `/ponytail` principle (*the simplest solution that actually works; reuse what already lives in the codebase*), we evaluated two paths:

### Option A: Immediate Rust Rewrite
- **Pros**: Compiler-enforced memory safety across `unsafe` boundaries, ergonomic crates (`crossbeam`).
- **Cons**: 4–6 week rewrite, risk of numerical/rule divergence against 420 golden vectors, new toolchain dependency in CI.
- **Verdict**: Deferred / Backlogged.

### Option B: Native C Standalone Core (`src/mdrap_core.c` + `fastpath.c`) (ACCEPTED)
- **Pros**:
  - Reuses the battle-tested, 100%-parity `fastpath.c` implementation immediately.
  - Zero toolchain friction: compiles cleanly via existing GCC/Clang/MSVC runners in seconds.
  - Delivers the primary architectural win (**zero interpreter frames, process isolation**) in hours rather than months.
  - Guaranteed bit-identical verdicts against all 420 golden test vectors.
- **Verdict**: Accepted for Phase 17/18. Rust rewrite remains on the architectural roadmap as a safety hardening upgrade once the process boundary is stabilized.
