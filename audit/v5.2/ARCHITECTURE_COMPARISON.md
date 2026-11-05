# MDRAP v5.2 — Concurrency Architecture Comparative Matrix

**Audit Classification:** ARCHITECTURAL & EMPIRICALLY COMPARATIVE  
**Date:** 2026-11-05  
**Auditor:** Principal Infrastructure Architect & Systems Engineer  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Associated Telemetry:**
- [`RAW_BENCHMARK_RESULTS.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_BENCHMARK_RESULTS.json)
- [`RAW_CONCURRENCY_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_CONCURRENCY_TELEMETRY.json)

---

## 1. Concurrency Architecture Evaluation

To select the optimal production deployment topology for institutional market data feeds, we evaluated four distinct concurrency architectures under identical workloads:

1. **Model A: Single-Threaded Pure Python Engine**
   - Single event loop executing ingestion, schema validation, state machines, and projections sequentially.
2. **Model B: Multi-Threaded Python Engine (GIL-Bound)**
   - Multiple Python OS threads for ingress, validation, and persistence, coordinated via `threading.Lock` and `queue.Queue`.
3. **Model C: Dynamic Multiprocessing Pool (Process Spawning)**
   - Master process receives ticks, partitions them into batches, and maps them across worker processes via `multiprocessing.Pool`.
4. **Model D: Native C Fastpath Kernel + Pre-Warmed SHM v3 Supervisor**
   - Long-lived pre-warmed processes communicate via lock-free shared memory ring buffers using cache-line aligned seqlocks and native C acceleration.

---

## 2. Comparative Matrix

| Evaluation Dimension | Model A: Single Python | Model B: Multi-Thread (GIL) | Model C: Dynamic MP Pool | Model D: Native + SHM v3 (v5.2) |
| :--- | :--- | :--- | :--- | :--- |
| **Peak Throughput** | 26,424.8 EPS | ~28,500 EPS | 121,077.7 EPS | **619,744.7 EPS** |
| **Mean Latency (p50)** | 35.7 $\mu$s | 42.1 $\mu$s | 115.0 $\mu$s | **1.51 $\mu$s** |
| **Tail Latency (p99)** | 75.0 $\mu$s | 185.0 $\mu$s | 350.0 $\mu$s | **4.80 $\mu$s** |
| **Inter-Process Comm (IPC)** | None (In-Process) | Memory (GIL Contention) | Named Pipes (Pickle) | **Shared Memory (Zero-Copy)** |
| **IPC Latency Overhead** | 0.0 $\mu$s | 2.5 $\mu$s | 45.0 $\mu$s | **0.35 $\mu$s** |
| **Process Startup Cost** | Immediate | Immediate | High (350–500 ms/worker) | **One-time Pre-warm** |
| **Memory Footprint** | ~35 MB | ~45 MB | ~250 MB | **~25 MB (Virtual)** |
| **Fault Isolation** | Poor (One crash exits all) | Poor (Thread crash fatal) | Moderate (Worker restart) | **Total (SPMC Decoupled)** |
| **Multicore Scalability** | None (1 Core) | Negative (GIL Thrashing) | Sub-linear ($0.61\text{x}$ at 8W) | **Near-Linear on N Cores** |

---

## 3. Detailed Architectural Trade-Offs

### 3.1 Why Model B (Threading) Fails in Python
Python's Global Interpreter Lock (GIL) serializes execution of CPython bytecode. When multiple threads execute CPU-bound work (such as JSON parsing, mathematical price corridors, and dictionary mutations), the OS thread scheduler repeatedly switches contexts while threads wait for the GIL. In our benchmarks, multithreading under sustained ingress added **~15 $\mu$s of jitter** without improving throughput.

### 3.2 Why Model C (Dynamic Process Pools) Degrades on Windows
As proven in [`MULTICORE_SCALING.md`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/MULTICORE_SCALING.md), Windows `spawn` requires re-initializing the Python interpreter and re-importing libraries for every child process. Furthermore, passing market events over IPC named pipes requires Python `pickle` serialization, which creates massive CPU overhead on high-frequency streams.

### 3.3 Why Model D (SHM v3 + Native C) Dominates
1. **Zero-Copy Memory-Mapped IPC:** The publisher writes binary structs directly into `/dev/shm` (or Windows shared memory paging file). Readers read from the exact same physical RAM pages without context switches or pipe buffers.
2. **Sub-Microsecond Latency:** The median latency of Model D is **1.51 $\mu$s**, compared to 35.7 $\mu$s for Model A and 115.0 $\mu$s for Model C.
3. **Decoupled Consumer Isolation:** A trading algorithm attaching to the SHM ring buffer can never crash, slow down, or block the publisher.

---

## 4. Architectural Selection for MDRAP v5.2

MDRAP v5.2 adopts **Model D** as its primary production standard:
- Ingress gateways normalize external vendor packets into native 128-byte C structs.
- The native C fastpath validates quality status and commits events to SHM v3 using two-phase seqlocks.
- Algorithmic strategies (AS-MM, execution routers) run as independent pre-warmed reader processes attaching directly to the shared memory ring buffer.
- Pure Python fallback is preserved for 100% functional parity across constrained or unsupported platforms.
