# MDRAP Hotpath Call Graph & Boundary Transition Analysis

## 1. Executive Summary

This document maps the exact hotpath call graphs of MDRAP, contrasting the current v4 execution paths with the proposed v5 unified zero-copy execution path.

The current platform exhibits two divergent processing paths:
1. **The Python Engine Hotpath (`Engine.submit()`)**: Complete transactional engine with undo-journaling, dict lookups, string formatting, and dynamic dataclass creation.
2. **The Fastpath Pipeline (`Pipeline.process_event()` / `FastQualityEngine`)**: Direct C extension invoking `fastpath.c:fastpath_evaluate_tick()`.

Our empirical micro-benchmarks (`audit/v5/raw_evidence/benchmark_micro_ffi.log`) reveal that:
- Pure C kernel execution takes **12 to 38 ns**.
- Python CFFI/ctypes struct-by-ref boundary transition takes **418 ns** (a **10x to 35x penalty** over the C kernel itself!).
- Python `Engine.submit()` takes **18,100 ns (18.1 $\mu$s)** p50 (`benchmark_engine_inmemory.log`).

---

## 2. Current Architecture Hotpath Call Graphs

### 2.1 The Python Engine Hotpath (`Engine.submit()`)

When an event is processed via `Engine.submit()` (or `Pipeline` with `use_engine=True`):

```mermaid
flowchart TD
    A["Engine.submit(event: RawEvent) [src/mdrap/engine.py:210]"] --> B["_validate_raw_event(event) [src/mdrap/engine.py:340]"]
    B --> C["_step_internal(event) [src/mdrap/engine.py:415]"]
    
    subgraph "Pure Python Transaction & Journaling"
        C --> D["Record undo action: append to self._undo_stack"]
        C --> E["Quality Rule Evaluation [src/mdrap/quality/engine.py:180]"]
        E --> F["PriceBandRule.evaluate() [src/mdrap/quality/rules.py:45]"]
        E --> G["CrossedBookRule.evaluate() [src/mdrap/quality/rules.py:82]"]
        E --> H["SpikeFilter.update_and_check() [src/mdrap/quality/spike.py:60]"]
        C --> I["State Dict Lookup & Update: self._state[symbol]"]
        C --> J["Deduplication Check: OrderedDict lookup"]
        C --> K["Construct CanonicalEvent() dataclass"]
        C --> L["Construct EngineDecision() dataclass"]
    end

    subgraph "Persistence & Event Sinks"
        A --> M["Storage.insert_canonical(canonical) [src/mdrap/storage/sqlite.py:280]"]
        M --> N["SQLite execute('INSERT INTO canonical_events...')"]
        A --> O["AuditLog.append(event) [src/mdrap/audit/logger.py:90]"]
    end
```

#### Exact Function Call Stack & Transition Costs:
1. `src/mdrap/engine.py:Engine.submit(event)`
   - Frequency: $1\times$ per ingested tick.
   - Transition: Pure Python method invocation ($\sim 150\ \text{ns}$).
2. `src/mdrap/engine.py:Engine._step_internal(event)`
   - Frequency: $1\times$ per tick.
   - Operations: Tuple allocation for undo-stack ($\sim 80\ \text{ns}$).
3. `src/mdrap/quality/engine.py:QualityEngine.evaluate(event)`
   - Frequency: $1\times$ per tick.
   - Operations: Iteration over list of rule objects, creating `RuleResult` instances ($\sim 4,900\ \text{ns}$).
4. `src/mdrap/quality/spike.py:SpikeFilter.update_and_check(price)`
   - Frequency: $1\times$ per tick.
   - Operations: Welford algorithm in pure Python float math ($\sim 320\ \text{ns}$).
5. `src/mdrap/engine.py:Engine._build_canonical(event, decision)`
   - Frequency: $1\times$ per valid tick.
   - Operations: Dataclass constructor `CanonicalEvent(...)` ($\sim 2,600\ \text{ns}$).
6. **Total In-Memory Duration `[BENCHMARK-MEASURED]`**:
   - Average: **$21.75\ \mu\text{s}$** ($45,958\ \text{EPS}$).
   - p50: **$18.10\ \mu\text{s}$**, p95: **$47.40\ \mu\text{s}$**, p99: **$82.70\ \mu\text{s}$**.

---

### 2.2 The Fastpath Pipeline Hotpath (`Pipeline.process_event()`)

When processed via `Pipeline` with native acceleration:

```mermaid
flowchart TD
    A["Pipeline.process_event(raw: RawEvent) [src/mdrap/pipeline.py:120]"] --> B["FastQualityEngine.evaluate(raw) [src/mdrap/quality/fast_engine.py:45]"]
    
    subgraph "Python-to-C FFI Boundary (418 ns)"
        B --> C["Pack RawEvent into ctypes/cffi TickInput struct"]
        C --> D["Call fastpath_evaluate_tick(&input, &output) [src/fastpath.c:85]"]
    end

    subgraph "Native C Hotpath Kernel (12 - 38 ns)"
        D --> E["fastpath_check_seq_dedup() [src/fastpath.c:140]"]
        D --> F["fastpath_eval_price_band() [src/fastpath.c:185]"]
        D --> G["fastpath_eval_welford() [src/fastpath.c:210]"]
        D --> H["fastpath_eval_crossed_book() [src/fastpath.c:240]"]
    end

    subgraph "Return Transition (ctypes unpack)"
        D --> I["Unpack TickOutput struct to Python QualityResult"]
    end

    subgraph "Downstream Pipeline"
        A --> J["Reconciler.reconcile(quality_result) [src/mdrap/reconciler.py:80]"]
        A --> K["shm_ring_write(&canonical) [src/fastpath.c:320]"]
    end
```

#### Micro-FFI Boundary Deconstruction `[BENCHMARK-MEASURED]`:
- From `audit/v5/raw_evidence/benchmark_micro_ffi.log`:
  - Python ctypes struct instantiation + packing: **$230\ \text{ns}$**.
  - FFI C call overhead: **$85\ \text{ns}$**.
  - Python ctypes struct unpacking: **$103\ \text{ns}$**.
  - **Total FFI boundary cost**: **$418.0\ \text{ns}$**.
  - **Native C kernel execution time**: **$38.8\ \text{ns}$**.
  - **FFI overhead ratio**: The boundary transition consumes **$91.5\%$** of the per-event processing time in the hybrid model! Calling C from Python for every single event creates an FFI tax that caps throughput at $\sim 1.5\text{--}2.0\ \text{M EPS}$, whereas the C kernel alone runs at **$25.74\ \text{M EPS}$**.

---

## 3. The Proposed MDRAP v5 Unified Native Hotpath

To unlock the native C kernel's full 25M–40M EPS capability and eliminate the 418 ns FFI tax, MDRAP v5 moves the entire hotpath loop into native C, maintaining Python strictly out-of-path for orchestration, configuration, and monitoring.

```mermaid
flowchart TD
    subgraph "Operating System & Wire"
        A["Network Ring Buffer / NIC Packet / SBE Frame"]
    end

    subgraph "v5 Native C Run-to-Completion Pipeline (Zero-Allocation)"
        A --> B["Ingest Loop: Read frame into pre-allocated NativeSlot [src/v5/ingress.c]"]
        B --> C["SBE / ITCH Binary In-Place Overlay [src/v5/sbe_overlay.c]"]
        C --> D["Feed Channel Sequence Gap Check [src/v5/sequencing.c]"]
        D --> E["Robin-Hood Deduplication Filter [src/v5/dedup.c]"]
        E --> F["fastpath_evaluate_tick() (AVX-2 Vectorized) [src/fastpath.c]"]
        F --> G["Commit Canonical Event Status [src/v5/engine_native.c]"]
        G --> H["Append to Ingestion WAL Ring [src/v5/ingest_wal.c]"]
        H --> I["Publish to Seqlock SHM Ring Buffer [src/v5/shm_ring.c]"]
        H --> J["Enqueue to SPSC Projection Queue [src/v5/spsc_ring.c]"]
    end

    subgraph "Downstream Async Consumption (Out-of-Path)"
        I --> K["External Ultra-Low-Latency Strategy Engine (SHM Consumer)"]
        J --> L["SQLite Projection Worker Thread [src/v5/sqlite_projector.c]"]
        L --> M["SQLite WAL Database (Micro-Batched executemany)"]
    end

    subgraph "Python Control Plane (Out-of-Path via IPC / Shared Stats)"
        N["Python CLI / Admin API / Telemetry Monitor"] -.->|Read atomic counters| O["SHM Stats Block (Counters, Gauges, Latency Histograms)"]
    end
```

### Call-to-Call Transition Costs in MDRAP v5:

| Call Boundary | Caller | Callee | Transition Mechanism | Latency Cost |
|:---|:---|:---|:---|:---:|
| Wire $\rightarrow$ Slot | OS Socket | `ingress.c:read_frame` | Zero-copy pointer advance | $20\text{--}45\ \text{ns}$ |
| Slot $\rightarrow$ Decoder | `ingress.c` | `sbe_overlay.c` | In-place struct pointer overlay | $< 5\ \text{ns}$ |
| Decoder $\rightarrow$ Seq | `sbe_overlay.c` | `sequencing.c` | Direct C inline function | $< 10\ \text{ns}$ |
| Seq $\rightarrow$ Dedup | `sequencing.c` | `dedup.c` | Bounded circular bitmask hash | $15\text{--}25\ \text{ns}$ |
| Dedup $\rightarrow$ Quality | `dedup.c` | `fastpath.c` | Direct C function call | $12\text{--}38\ \text{ns}$ |
| Quality $\rightarrow$ WAL | `fastpath.c` | `ingest_wal.c` | Sequential memory write + CRC32C | $45\text{--}80\ \text{ns}$ |
| WAL $\rightarrow$ SHM | `ingest_wal.c` | `shm_ring.c` | Seqlock write (`atomic_store`) | $65\text{--}110\ \text{ns}$ |
| WAL $\rightarrow$ Queue | `ingest_wal.c` | `spsc_ring.c` | Relaxed atomic head store | $15\text{--}30\ \text{ns}$ |
| **Total In-Memory Hotpath** | | | **Zero Python FFI / Zero GIL** | **$\mathbf{187\text{--}343\ \text{ns}}$** |

---

## 4. Architectural Invariants Preserved in Hotpath

1. **No Python Object Instantiation on Ingress**:
   - `RawEvent`, `RuleResult`, and `CanonicalEvent` dataclasses are completely eliminated from the C hotpath. Data resides in contiguous 128-byte cache-aligned C structs (`NativeSlot`).
2. **Batching FFI Boundaries if Python Consumers Exist**:
   - If Python consumers require canonical events, events are transferred in contiguous batches of $N = 1,000$ slots via a single pointer pass (`ctypes.POINTER(NativeSlot * 1000)`), reducing the per-event FFI overhead from $418\ \text{ns}$ to $0.418\ \text{ns}$ per tick!
3. **Run-to-Completion Pinned Worker**:
   - The entire path from Wire Ingress to SHM Publication executes on a single pinned Performance Core (P-Core), eliminating thread context switching and L1/L2 cache evictions.
