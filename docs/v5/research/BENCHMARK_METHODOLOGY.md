# MDRAP v5.0 — Benchmark Methodology & Latency Measurement Standards

**Document ID:** `docs/v5/research/BENCHMARK_METHODOLOGY.md`  
**Classification:** Research Analysis & Benchmarking Governance  
**Auditor / Architect:** Principal Low-Latency Systems Architect & Performance Benchmarking Specialist  
**Evidence Standard:** Primary Benchmarking Literature (Gil Tene, Google Benchmark) & Hardware Profiling  

---

## 1. Primary References & Industry Principles

- **Primary Source:** Gil Tene (Azul Systems) — *Understanding Latency and the Coordinated Omission Problem*
  - HdrHistogram: https://github.com/HdrHistogram/HdrHistogram
- **Google Benchmark User Guide:** https://github.com/google/benchmark/blob/main/docs/user_guide.md
- **Linux Perf Tooling:** https://man7.org/linux/man-pages/man1/perf-stat.1.html

---

## 2. The Coordinated Omission Flaw in Market Data Benchmarking

The single most common error in latency benchmarking is **Coordinated Omission**:

```
CLOSED-LOOP BENCHMARK (FLAWED):
[Submit Event 1] ──► [Wait for Response (10us)] ──► [Submit Event 2] ──► [Wait (10ms pause)] ──► [Submit Event 3]
Result: Only 1 event was measured during the 10ms stall! Average latency appears falsely low!

OPEN-LOOP BENCHMARK (CORRECT):
[Scheduled Ticks: T0, T0+10us, T0+20us, ...]
[Submit Event 1] ──► [Wait for Response (10us)]
[Engine Stalls for 10ms] ──► 1,000 events queue up!
Result: All 1,000 queued events record their actual end-to-end delay (up to 10ms)! True tail latency is exposed!
```

### Mandatory Testing Standards in MDRAP v5.0
1. **Service Time vs. End-to-End Latency**:
   - **Service Time**: Duration from when a worker starts processing an event until completion ($t_{finish} - t_{start}$).
   - **Queueing Time**: Duration an event sits in a buffer waiting for a worker ($t_{start} - t_{arrival}$).
   - **End-to-End Latency**: $T_{\text{service}} + T_{\text{queueing}} = t_{finish} - t_{arrival}$.
2. **Open-Loop Load Generation**:
   - Benchmarks evaluating capacity must inject events at target offered rates (e.g. 50k, 100k, 200k EPS) independently of the system's processing speed.
   - If the system experiences a 5ms GC or checkpoint pause, the queue builds up, and the latency of subsequent events reflects the delay.
3. **High Dynamic Range (HDR) Latency Capture**:
   - Avoid downsampling or averaging. Report p50, p90, p95, p99, p99.9, and maximum latency.

---

## 3. Strict Architectural Benchmark Boundaries

To ensure scientific honesty, MDRAP v5 mandates **12 strictly isolated benchmark boundaries**:

| Boundary ID | Target Subsystem | Components Included | Excluded Components |
| :---: | :--- | :--- | :--- |
| **A** | **Native C Kernel Only** | `fastpath.c` Welford variance, sequence bitmask, dedup | FFI, Python, Disk, SHM |
| **B** | **Python / C FFI Boundary** | ctypes / C-API argument passing & unboxing | Disk, SQLite, Python Engine |
| **C** | **In-Memory Engine Step** | Ingest $\rightarrow$ Normalize $\rightarrow$ Quality $\rightarrow$ State fold | Disk WAL, Projections, SHM |
| **D** | **Ingress & Protocol Decode** | SBE binary / JSON string parsing into event struct | State update, Quality, Disk |
| **E** | **Ingress Through In-Memory State** | Ingress + Decode + Quality + State update | Disk WAL, Projections |
| **F** | **WAL Append (Buffered)** | Sequential binary WAL frame assemble & OS write (`never`) | `fsync`, Projections |
| **G** | **WAL Group Commit** | WAL frame assemble + Background batch `fsync` | Single-event `fsync` |
| **H** | **WAL Per-Event Physical Fsync** | WAL frame assemble + Immediate synchronous `fsync` (`always`) | Group commit |
| **I** | **Full Projection Pipeline** | WAL append + Asynchronous SQLite projection apply | Standalone in-memory engine |
| **J** | **Shared-Memory IPC Delivery** | Native C SHM seqlock write + client reader poll | Relational database, network |
| **K** | **Multi-Worker Partitioned** | Channel sequencer $\rightarrow$ 4 worker queues $\rightarrow$ state updates | Monolithic single-thread lock |
| **L** | **Full End-to-End Workload** | Ingress $\rightarrow$ Quality $\rightarrow$ WAL $\rightarrow$ SQLite + SHM | None (Complete production path) |

**Rule**: Never cite a boundary A (pure C kernel) benchmark as the performance of boundary L (full end-to-end pipeline).

---

## 4. Hardware Core Topology & Hybrid CPU Scheduling

### 4.1 Intel Alder Lake / Raptor Lake Core Asymmetry
In the benchmark environment:
- **Processor**: Intel Core i7-1255U (Family 6 Model 154)
- **Topology**:
  - **2 Performance Cores (P-cores)**: Cores 0, 1 (Logical Threads 0, 1, 2, 3 with Hyper-Threading). High IPC, AVX2, high clock frequency (up to 4.7 GHz).
  - **8 Efficient Cores (E-cores)**: Cores 2–9 (Logical Threads 4–11, single-threaded). Shared L2 cache clusters, lower clock frequency (up to 3.5 GHz).

### 4.2 Thread Affinity Strategy for v5.0
`[SOURCE-VERIFIED]` Operating without thread affinity on hybrid CPUs causes severe performance instability:
- If the OS scheduler migrates the critical feed ingestion thread from a P-core to an E-core mid-run, throughput drops by 30–50% and tail latency spikes by 3x.
- **Affinity Policy**:
  - **P-Core 0 (Thread 0)**: Dedicated Feed Ingress & Channel Sequencing.
  - **P-Core 1 (Thread 2)**: Dedicated Instrument Processing Hot Path (or SPSC Worker 1).
  - **E-Cores (Threads 4–11)**: Background WAL flushers, asynchronous SQLite projection workers, telemetry, and web API handlers.

---

## 5. Evidence Classification Summary

- `[EXTERNAL-RESEARCH]` Gil Tene Coordinated Omission analysis and Google Benchmark guide.
- `[SOURCE-VERIFIED]` `benchmarks/stage_breakdown.py`, `benchmarks/micro_ffi.py`, `benchmarks/check_regression.py`.
- `[BENCHMARK-MEASURED]` `audit/v5/raw_evidence/system_diagnostics.json`.
