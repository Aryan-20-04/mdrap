# MDRAP v5.2 — Benchmark Methodology & Measurement Standards

## 1. Experimental Environment & System Specifications

```
+---------------------------------------------------------------------------------------------------------+
|                                    HARDWARE & RUNTIME SPECIFICATION                                     |
+--------------------------------+------------------------------------------------------------------------+
| Parameter                      | Value / Configuration                                                  |
+--------------------------------+------------------------------------------------------------------------+
| Host Operating System          | Microsoft Windows 11 Pro 64-bit (Build 10.0.26100)                     |
| Processor (CPU)                | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz                         |
| Core Architecture              | 4 Physical Cores / 8 Logical SMT Threads (Tiger Lake)                  |
| Cache Hierarchy                | L1 Data: 4x 48 KB, L1 Inst: 4x 32 KB, L2: 4x 1.25 MB, L3: 8 MB Shared   |
| System Memory (RAM)            | 16 GB DDR4-3200 (Dual Channel)                                         |
| Storage Subsystem              | NVMe PCIe 3.0 x4 Solid State Drive                                     |
| Python Runtime                 | CPython 3.13.1 (64-bit, MSC v.1942)                                    |
| C Compiler                     | Microsoft Visual C++ (MSVC) 19.42 (Visual Studio 2022)                 |
| Compiler Optimization Flags    | /O2 (Max Speed) /Oi (Intrinsic Functions) /Ot (Favor Fast Code) /MD   |
+--------------------------------+------------------------------------------------------------------------+
```

---

## 2. Measurement Boundaries & Isolation Rigor

A major failure of prior benchmark reports was conflating distinct measurement boundaries under the umbrella term "pipeline throughput". In MDRAP v5.2, every benchmark is strictly classified into one of five isolated architectural boundaries:

```
[Raw Wire Event]
      │
      ▼  ─── Boundary A: Deserialization & Normalization (JSON/SBE -> CanonicalEvent)
[CanonicalEvent]
      │
      ▼  ─── Boundary B: Quality Rules Engine (Validation Invariants: C vs Python)
[Validated Event]
      │
      ▼  ─── Boundary C: Stateful Engine & Window Management (engine.step() + Lineage)
[Engine State]
      │
      ▼  ─── Boundary D: IPC Distribution (SHM v3 Seqlock Ring Buffer Write + Read)
[Consumer Read]
      │
      ▼  ─── Boundary E: Persistence & Durability (Binary WAL append / Direct fsync)
[Disk NVMe]
```

### Boundary Definitions:
1. **Boundary A+B (Stateless Ingress & Quality)**:
   - Measures raw throughput of `normalize(raw)` + `FastQualityEngine.evaluate(canon)`.
   - Excludes state tracking, order book history, IPC, and disk writes.
   - Verified Throughput: **~65,000 EPS** (Single-core).
2. **Boundary A+B+C+D (Full E2E In-Memory Pipeline)**:
   - Measures `raw -> engine.step() -> FastQualityEngine -> state transition -> lineage -> SHMWriter.write_tick -> SHMReader.read_slot`.
   - Complete pipeline before persistent storage.
   - Verified Throughput: **~26,425 EPS** (Single-core).
3. **Boundary E1 (Buffered In-Memory WAL)**:
   - Append-only binary log with userspace segment buffering (`fsync_policy="never"`).
   - Verified Throughput: **~93,726 EPS**.
4. **Boundary E2 (Durable NVMe Fsync WAL)**:
   - Direct physical storage commit with OS `fsync` after every record (`fsync_policy="always"`).
   - Hardware-bound by NVMe barrier and flush latency: **~336.6 EPS** (~2.8 ms per fsync).
5. **Boundary D (Isolated SHM v3 Ring Buffer)**:
   - Two-phase commit seqlock memory transport (`write_tick` + `read_slot`).
   - Verified Throughput: **~300,293 EPS**.

---

## 3. Timing Mechanics & Statistical Rigor
- **Clock Source**: All per-event latency timing uses `time.perf_counter_ns()` with hardware RDTSC resolution (<10 nanoseconds).
- **Warm-Up Protocol**: All benchmarks discard the first 200–500 iterations to prime CPU instruction cache (L1I), TLB pages, and Python bytecode caches.
- **Percentile Metrics**: Latencies are sorted to report exact percentiles:
  - $p50$ (Median), $p90$, $p95$, $p99$, $p99.9$, and Maximum.
- **Reciprocal Fallacy Rule**: We explicitly reject computing latency as $\frac{1}{\text{Throughput}}$. Throughput captures amortized batched bandwidth; per-event latency measures the exact elapsed time from ingress to egress.
