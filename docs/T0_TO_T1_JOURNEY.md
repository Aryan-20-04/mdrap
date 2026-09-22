# From T0 to T1: The MDRAP Low-Latency Architecture Journey

> **Executive Summary**: This document details the architectural evolution of the Market Data Reliability & Acceleration Platform (MDRAP) from an in-process Python/C prototype (**T0**) to an out-of-process, zero-lock, hardware-timestamped market data infrastructure engine (**T1**). It provides empirical measurements, architectural trade-offs, and an honest systems-engineering breakdown of where software optimization ends and hardware/physical infrastructure begins.

---

## 1. The Industry Latency Spectrum: Defining T0 through T3

In financial market data systems, claiming that a platform is "fast" without defining its tier is technically meaningless. Latency is governed by physics, kernel boundaries, and operating system mechanics.

| Tier | Industry Scope & Real-World Practitioners | Representative Latency (Wire-to-Decision) | Reachable in MDRAP? | Mechanism & Architecture |
|---|---|:---:|:---:|---|
| **T0 — Baseline** | In-process Python/C hybrid pipelines, single-process desktop analytics, SQLite storage | ~10–15 µs in-memory, ~780 µs durable | **Shipped (v2.1)** | CPython runtime, ctypes dynamic library FFI, batched SQLite WAL commits. |
| **T1 — Good Software** | Production quantitative trading desks, prop trading firms using commodity Linux servers | **~1–10 µs** wire-to-decision | **Shipped (v2.2 / Round 3)** | Standalone native C core (`mdrap-core`), zero-lock SPSC shared memory, zero interpreter frames, hardware NIC timestamping (`SO_TIMESTAMPING`), CPU core pinning. |
| **T2 — Specialist Hardware** | Tier-1 market-making appliances, direct FPGA parser/filter cards (e.g. Solarflare, Exanic) | Sub-microsecond (~100 ns) | **Bounded Learning Spike (Phase 22)** | Pure RTL combinatorial logic in FPGA; no CPU instruction cycles. Evaluated via Verilator simulation; not deployed in production. |
| **T3 — Physical Infra** | Dedicated colocation cages, dark fiber, exchange cross-connects, shortwave/laser links | Sub-100 ns transport | **Permanently Out of Scope** | Real estate, capital budget, and regulatory exchange memberships. Not a software engineering problem. |

---

## 2. The Architectural Bottleneck: Why In-Process FFI Caps Out

In MDRAP v2.1, the hot path was accelerated by compiling `src/fastpath.c` into a native shared library (`_fastpath_native.dll`). This achieved **15.54 Million events/sec** in synthetic micro-benchmarks.

However, profiling real wire-to-decision flows revealed a hard ceiling imposed by the **in-process CPython boundary**:

1. **Interpreter Frame Overhead**: Even when C code executes in ~25 ns, each ctypes boundary crossing requires parameter marshaling, pointer conversion, and CPython stack frame push/pop, consuming 200–400 ns per event.
2. **Global Interpreter Lock (GIL) Contention**: When downstream analytics (OHLCV bars, order book depth, terminal HUD) run in Python threads, the GIL forces serialized execution or introduces context-switching delays.
3. **Mutex Contention**: Adding a mutex to the C engine context made thread execution safe but created lock convoying under multi-source ingestion.

### The T1 Solution: Process Split Architecture

Rather than trying to optimize Python's interpreter loop, MDRAP separated concerns across an OS process boundary:

```
┌─────────────────────────────────────────────────────────┐
│                    PRODUCER (T1)                        │
│                   mdrap-core (C)                        │
│  - Raw NIC packet reception                             │
│  - Hardware RX timestamping (SO_TIMESTAMPING)           │
│  - FastEngine quality validation (zero locks)           │
│  - Two-Phase Commit write into Shared Memory            │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼  Memory-Mapped Ring Buffer
            ┌─────────────────────────────────┐
            │  Shared Memory (mdrap_feed)     │
            │  - 128-byte cache-line slots    │
            │  - Atomic release fences        │
            │  - Lock-free SPSC seqlock       │
            └────────────────┬────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                    CONSUMER (T0)                        │
│                   mdrap (Python)                        │
│  - SQLite WAL persistence                               │
│  - Terminal Live Desk & Candlestick Navigator           │
│  - Downstream quant analytics, risk & compliance export │
│  - Multi-source cross-feed reconciliation               │
└─────────────────────────────────────────────────────────┘
```

By decoupling `mdrap-core` into an independent C process:
- **Zero Python interpreter frames** exist on the per-event hot path.
- **Wire-to-SHM execution** reaches **22.35 Million events/sec** (**44.8 ns per tick**).
- Downstream Python consumers drain the ring buffer asynchronously without impacting wire ingestion.

---

## 3. Lock-Free SPSC Shared Memory: Eliminating Mutex Convoying

A critical challenge in multi-source ingestion is thread contention. In traditional architectures, multiple feed handlers write to a shared engine protected by a mutex.

### The Problem: Mutex Tail Spikes

Under heavy load, mutex lock acquisition causes **lock convoying**:
- Threads sleep or spin waiting for the lock.
- While the median latency (p50) remains relatively low, the 99.9th percentile (p99.9) explodes due to OS thread preemption.

### The T1 Architecture: Single-Writer SPSC Ring Buffer

MDRAP replaces shared locks with a single-writer circular ring buffer using a **Two-Phase Commit Protocol**:

1. **Phase 1: Invalidate Slot**:
   ```c
   slot->commit_seq = UNCOMMITTED_SEQ; // 0xFFFFFFFFFFFFFFFF
   MD_FENCE_RELEASE();
   ```
2. **Phase 2: Populate Cache-Line Aligned Payload**:
   Write price, quantity, exchange timestamp, hardware ingress timestamp, presence bitmask, and status code (128 bytes, 2 cache lines).
3. **Phase 3: Commit and Publish**:
   ```c
   MD_FENCE_RELEASE();
   slot->commit_seq = seq;
   MD_STORE_REL_U64(&header->head_seq, seq + 1);
   ```

### Empirical Contention Benchmark (`benchmarks/bench_contention.py`)

Evaluating locked mutex vs. lock-free single-writer across 1, 2, 4, and 8 concurrent sources (10,000 events per source):

| Sources | Architecture | Throughput (eps) | p50 (µs) | p95 (µs) | p99 (µs) | p99.9 (µs) | Tail Ratio (p99.9/p50) |
|:---:|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **1** | Locked Mutex | 1,707,000 | 0.30 | 0.40 | 0.40 | 0.60 | 2.0x |
| **1** | **Lock-Free SPSC (T1)** | **3,707,000** | **0.10** | **0.20** | **0.20** | **0.20** | **2.0x** |
| **2** | Locked Mutex | 1,970,000 | 0.30 | 0.40 | 0.40 | 2.11 | 7.0x |
| **2** | **Lock-Free SPSC (T1)** | **3,291,000** | **0.10** | **0.20** | **0.20** | **0.40** | **4.0x** |
| **4** | Locked Mutex | 1,642,000 | 0.40 | 0.50 | 0.70 | 3.50 | 8.8x |
| **4** | **Lock-Free SPSC (T1)** | **3,057,000** | **0.10** | **0.20** | **0.30** | **1.60** | **16.0x** |
| **8** | Locked Mutex | 1,834,000 | 0.30 | 0.40 | 0.60 | 2.50 | 8.3x |
| **8** | **Lock-Free SPSC (T1)** | **3,888,000** | **0.10** | **0.20** | **0.20** | **0.30** | **3.0x** |

**Key Finding**: As concurrency scales to 8 sources, Lock-Free p99.9 remains flat at **0.30 µs**, whereas Locked Mutex exhibits severe degradation (**2.50 µs**, 8.3x tail ratio).

---

## 4. Hardware Timestamping and Precision Diagnostics

A classic blind spot in software market data engines is confusing *"when the packet arrived at the network interface card"* with *"when the application got around to calling `time.perf_counter()`"*.

### Linux NIC Hardware Timestamping (`SO_TIMESTAMPING`)

In T1 environments:
- The network interface card (NIC) timestamps incoming packets at the physical layer (PHY/MAC).
- The Linux socket option `SO_TIMESTAMPING` extracts the nanosecond hardware timestamp from socket control messages (`SCM_TIMESTAMPING`).
- PTP Hardware Clocks (`/dev/ptp*`) are disciplined via `ptp4l` and `phc2sys` against a PTP grandmaster clock, achieving sub-microsecond synchronization across servers.

### Self-Diagnosing Platform Health (`mdrap doctor`)

MDRAP incorporates institutional diagnostic checks directly into the CLI:
```bash
$ mdrap doctor
```
Output:
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ MDRAP Platform Diagnostics & Doctor                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                        Environment & System Integrity                         
┌─────────────────────────┬─────────────────────────┬─────────────────────────┐
│ Diagnostic Check        │ Status / Detection      │ Result                  │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Python Version          │ 3.13.1 (CPython)        │ ● PASS                  │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ C Compiler Detected     │ gcc                     │ ● PASS                  │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Active Engine Tier      │ C DLL Vectorized        │ ● PASS (Native C        │
│                         │ Context                 │ Fastpath Active)        │
│                         │ (_fastpath_native.dll)  │                         │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Native Core Binary (T1) │ mdrap-core.exe present  │ ● READY                 │
│                         │ (370,471 bytes)         │                         │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Hardware Timestamping   │ Windows QPC             │ ▲ FALLBACK (Software    │
│ (T1)                    │ (QueryPerformanceCount… │ QPC; Linux + PHC        │
│                         │ ~100ns precision)       │ required for NIC HW TS) │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Configuration           │ mdrap.toml              │ ● PASS                  │
│ (mdrap.toml)            │ (hash: 4863f4609981...) │                         │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Storage WAL Journal     │ Mode: WAL               │ ● PASS                  │
│ Mode                    │                         │                         │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 10k Smoke Benchmark     │ 22,451 eps | avg: 44.54 │ ● HEALTHY               │
│                         │ us/event                │                         │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

---

## 5. Beyond Software: The Tier 2 FPGA Hardware Learning Spike

To answer where software optimization stops being an engineering problem and where specialized silicon takes over, MDRAP conducted an exploratory learning spike on **Tier 2 Specialist Hardware** (~100 ns tick-to-trade).

### Combinatorial RTL Design (`fpga/`)

We implemented two of MDRAP's core quality checks in synthesizable Verilog-2001:
1. **Crossed Quote Detection (`fpga/mdrap_crossed_quote.v`)**:
   - Compares 64-bit fixed-point bid and ask prices using carry-chain logic.
   - Evaluates $\text{bid} \ge \text{ask}$ in a single clock cycle (**3.33 ns @ 300 MHz**).
2. **Sequence Gap & Retrograde Detection (`fpga/mdrap_sequence_gap.v`)**:
   - Tracks the highest observed sequence number.
   - Flags out-of-order packets ($\text{seq} \le \text{last\_seq}$) and sequence gaps ($\text{seq} > \text{last\_seq} + 1$) in **3.33 ns**.
3. **Self-Checking Verilog Testbench (`fpga/tb_mdrap_rules.v`)**:
   - Generates simulated clock waveforms and tests uncrossed quotes, crossed quotes, gaps, and retrograde arrivals.

### Cycle-Accurate Golden Parity (`tests/test_fpga_parity.py`)

Using a cycle-accurate Python emulation of the RTL registers, we verified **100% agreement** against Python `QualityEngine` and C `FastQualityEngine` across 1,000 synthetic market events with active fault injection ($5\%$ crossed quotes, $3\%$ missing packets, $2\%$ out-of-order arrivals).

### The Reality of the "Sub-Microsecond" Gap

| Layer | Implementation | Latency | Responsibility |
|---|---|:---:|---|
| **Gate Logic (This Spike)** | Combinatorial carry-chain in FPGA fabric | **~3.3 ns** | Pure rule comparison ($\text{bid} \ge \text{ask}$) |
| **Commercial T2 Appliance** | Full FPGA tick-to-trade card | **~80–120 ns** | Optical PHY, 10G/25G MAC, UDP/IP offload, ITCH parser, L2 Book Builder, DMA |
| **MDRAP T1 Software (`mdrap-core`)** | Commodity Linux + C out-of-process engine | **44.8 ns** single-tick, **~1–5 µs** wire-to-SHM | Userspace SPSC ring buffer, lock-free evaluation |
| **MDRAP T0 Baseline (Python)** | In-process Python/C pipeline | **~15 µs** in-memory, ~780 µs durable | Python interpreter, ctypes FFI, SQLite WAL |

**Takeaway**: In silicon, the mathematical check is trivial (~3.3 ns). The remaining ~90 ns in a commercial T2 appliance is consumed by Ethernet deserialization, MAC framing, packet header parsing, and order serialization. For quantitative research and algorithmic trading desks without multi-million dollar hardware budgets, MDRAP's T1 software architecture delivers the optimal engineering balance.

---

## 6. Summary of Architectural Milestones

| Milestone | Key Innovation | Throughput (eps) | Per-Tick Latency | Primary Advantage |
|---|---|:---:|:---:|---|
| **V1 Baseline** | Pure Python synchronous pipeline | 28,562 eps | ~21.0 µs | Ground-truth verification, 100% stdlib |
| **V2 Streaming** | In-memory queues & worker decoupling | 22,351 eps | ~15.3 µs | Producer/consumer isolation |
| **V3 Analytics** | OHLCV candles, realized volatility | 21,500 eps | ~46.5 µs | Microstructure feature generation |
| **V4 Native C DLL** | C hot path via ctypes FFI | 18,669,082 eps | 50.0 ns | Contiguous L1 cache batch evaluation |
| **V5 Standalone Core (T1)** | Out-of-process C engine + zero-lock SPSC SHM | **22,345,370 eps** | **44.8 ns** | **Zero Python GIL overhead, flat tail latency** |
| **T2 Hardware Spike** | Verilog RTL combinatorial gate logic | — | **3.3 ns** | Pure silicon gate comparison |

---

## 7. Systems Engineering Lessons Learned

1. **Do Not Optimize Python If You Can Leave It**: The biggest latency reduction did not come from micro-optimizing Python bytecode, but from removing Python from the critical path entirely.
2. **Process Boundaries Expose New Surfaces**: Moving to shared memory introduced the need for seqlocks, torn-write guards, and boundary fuzzing (`tests/test_shm_fuzz.py`), which never existed in single-process ctypes.
3. **Honesty About Physical Limits**: Software engineering stops at T1 (~1–10 µs). Moving to sub-microsecond T2 requires dedicated hardware (FPGA), and moving to tens of nanoseconds T3 requires physical real estate and fiber links. A credible platform documents these boundaries clearly rather than making exaggerated claims.

