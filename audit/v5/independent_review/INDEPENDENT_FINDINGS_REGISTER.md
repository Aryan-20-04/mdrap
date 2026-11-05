# MDRAP v5.0 — Independent Findings Register (P0–P4)

**Audit Date**: 2026-11-05  
**Auditor**: Independent Principal Systems Engineer & Quality Auditor  
**Classification**: `[FINDINGS-REGISTER]`

---

## 1. Severity & Evidence Classification Taxonomy

### Severity Definitions
- **P0 (Critical)**: Catastrophic data corruption, silent data loss, critical security exploit, or non-deterministic recovery failure.
- **P1 (Major Blocker)**: Significant defect preventing reliable production deployment, broken API contract, or unhandled crash condition.
- **P2 (Significant Weakness)**: Architectural or concurrency limitation restricting scale, portability, or thread safety under specific runtimes.
- **P3 (Moderate Issue)**: Performance bottleneck, benchmark measurement imperfection, or maintainability debt.
- **P4 (Minor / Improvement)**: Stylistic, documentation ambiguity, or minor logging enhancement.

### Evidence Classifications
- `SOURCE-VERIFIED`: Directly observed and verified in source code.
- `TEST-EXECUTED`: Validated via execution of a reproducible test case.
- `BENCHMARK-MEASURED`: Measured and quantified on host hardware via empirical benchmark.
- `STATIC-ANALYSIS`: Identified through compiler, linter, or AST inspection.
- `INFERRED`: Derived logically from system invariants.
- `UNVERIFIED`: Claimed in documentation but lacking empirical proof.

---

## 2. Comprehensive Findings Register

### Finding F-01: `SpscRingQueue` Relies on CPython GIL for Memory Ordering
- **Severity**: **P2 (Significant Weakness)**
- **Evidence Classification**: `SOURCE-VERIFIED`
- **Affected Subsystem**: Concurrency & Partitioning (`src/mdrap/partition.py`)
- **Exact Location**: `src/mdrap/partition.py:35-82` (`class SpscRingQueue`)
- **Technical Explanation**: `SpscRingQueue` updates its head pointer (`self._head = head + 1`) and tail pointer (`self._tail = tail + 1`) using pure Python attribute assignment. In standard CPython, the Global Interpreter Lock enforces atomic bytecode execution and acts as a global fence. However, in free-threaded Python 3.13 (PEP 703 `python3.13t` with GIL disabled), attribute writes do not generate CPU acquire/release memory barriers. On weakly-ordered CPU architectures (such as ARM64), memory stores can be reordered by the CPU or compiler, allowing the consumer thread to read an uncommitted buffer slot.
- **Reproduction Procedure**: Inspect bytecode and memory fences in `offer()` and `poll()`. Run under `python3.13t` with concurrent saturation.
- **Actual Observed Evidence**: Source inspection confirms no calls to native C atomics or `threading.Lock()` exist in `SpscRingQueue`.
- **Consequence**: Unsafe for deployment on free-threaded Python (nogil) or weakly-ordered CPUs without GIL.
- **Recommended Remediation**: Implement native C SPSC ring buffer extension (`_fastpath_spsc.c`) utilizing C11 `__atomic_store_n(head, RELEASE)` and `__atomic_load_n(head, ACQUIRE)` for v5.1.
- **Validation Test**: `tests/test_channel_sequencing_partition.py::TestSpscRingQueue::test_spsc_concurrent_producer_consumer` executed under free-threaded Python build.

---

### Finding F-02: Release Manifest Conflates Total Test Executions with Unique Test Nodes
- **Severity**: **P3 (Audit Precision / Documentation Ambiguity)**
- **Evidence Classification**: `SOURCE-VERIFIED & TEST-EXECUTED`
- **Affected Subsystem**: Test Harness & Release Gate (`scripts/release_gate.py`, `audit/v5/RELEASE_CERTIFICATION.md`)
- **Exact Location**: `scripts/release_gate.py:186-272`, `audit/v5/RELEASE_CERTIFICATION.md:14`
- **Technical Explanation**: Previous certification reports claimed "1,770 total executions" in the headline without clarifying that this represents 1,672 unique test nodes plus 98 duplicate specialized gate executions (Gates 6, 7, 8, 9, 9b). All 98 specialized gate tests were already executed as part of Gate 5's collection.
- **Reproduction Procedure**: Run node collection reconciliation script comparing node IDs of Gate 5 vs Gates 6–9b:
  `python -c "..."` (see `PROVENANCE_RECONCILIATION.md`).
- **Actual Observed Evidence**:
  - Gate 5 collected: 1,672 unique tests.
  - Gates 6–9b collected: 98 tests, all 98 of which overlap 100% with Gate 5.
  - Active unique test count in repo: 1,672. Marker-deselected tests: 63. Total repo tests: 1,735.
- **Consequence**: An external release auditor could mistakenly infer that 1,770 distinct test scenarios exist in the repository.
- **Recommended Remediation**: Standardize reporting to distinguish "Unique Test Nodes (1,672)" from "Total Gate Executions (1,770)".
- **Validation Test**: `python scripts/release_gate.py` node reconciliation table.

---

### Finding F-03: Microbenchmark Observer Overhead in Tight Per-Tick Timing Loops
- **Severity**: **P3 (Measurement Methodology)**
- **Evidence Classification**: `BENCHMARK-MEASURED`
- **Affected Subsystem**: Benchmarking Harness (`benchmarks/check_regression.py:86`, `audit/v5/performance/REPRODUCTION_SCRIPTS/run_12_benchmarks.py`)
- **Exact Location**: `benchmarks/check_regression.py:86-89`
- **Technical Explanation**: In the regression benchmark loop, `time.perf_counter_ns()` is invoked immediately before and after each event. On Windows 11 AMD64, `perf_counter_ns()` incurs $\sim 25\text{ ns}$ per call, introducing $\sim 50\text{ ns}$ of measurement overhead per event. For operations taking $1.5\text{--}2.5\text{ }\mu\text{s}$, this represents $2.0\text{--}3.5\%$ observer overhead and pollutes L1 instruction cache.
- **Reproduction Procedure**: Measure 1,000,000 sequential `perf_counter_ns()` calls in isolation; observe 25.4 ns average call duration.
- **Actual Observed Evidence**: In `run_benchmark()`, 50,000 loop iterations trigger 100,000 clock queries and 50,000 dynamic list appends.
- **Consequence**: Reported per-event latencies in microbenchmarks are slightly inflated by clock query overhead.
- **Recommended Remediation**: Adopt block-amortized timing (timing 1,000 operations per clock query) for microbenchmarks in v5.1.
- **Validation Test**: Compare microbenchmark results with RDTSC hardware counter measurements.

---

### Finding F-04: Kernel Bypass and Hardware PTP Networking are Simulation Models
- **Severity**: **P2 (Architectural Boundary)**
- **Evidence Classification**: `SOURCE-VERIFIED`
- **Affected Subsystem**: Protocol & Hardware Integration (`src/mdrap/af_xdp.py`, `src/mdrap/clock.py:PTPClock`)
- **Exact Location**: `src/mdrap/af_xdp.py:1-150`, `src/mdrap/clock.py:180-220`
- **Technical Explanation**: `af_xdp.py` implements a software simulation of AF_XDP UMEM packet memory and chunk ring descriptors; it does not bind to Linux kernel eBPF XDP drivers. Similarly, `PTPClock` models PTP time offsets in software rather than extracting live hardware PTP timestamps from a Solarflare/Mellanox NIC grandmaster oscillator.
- **Reproduction Procedure**: Inspect imports and execution in `af_xdp.py`; run without Linux root/CAP_NET_RAW.
- **Actual Observed Evidence**: Module executes cleanly in user space on Windows 11 without root privileges or hardware drivers.
- **Consequence**: MDRAP cannot achieve sub-microsecond wire-to-wire packet capture on standard OS sockets without true kernel bypass drivers.
- **Recommended Remediation**: Document `af_xdp.py` as an architectural simulation layer and build a native C `libbpf` driver in Phase F of v5.1.
- **Validation Test**: `tests/test_af_xdp.py` test suite.

---

### Finding F-05: Single-Core Throughput Bounded by Python Orchestration Loop
- **Severity**: **P3 (Performance Scaling)**
- **Evidence Classification**: `BENCHMARK-MEASURED`
- **Affected Subsystem**: Core Processing Engine (`src/mdrap/engine.py:1015-1160`)
- **Exact Location**: `src/mdrap/engine.py:submit()`
- **Technical Explanation**: While native C fastpath validates ticks at $> 400,000\text{ EPS}$, the top-level `Engine.submit()` orchestration—which unpacks dictionaries, performs session lookups in Python dicts, updates transaction history, and records telemetry—is executed in Python. This caps single-core in-memory ingestion at $\sim 29,200\text{ EPS}$ (full pipeline with WAL at $\sim 14,000\text{ EPS}$).
- **Reproduction Procedure**: Run cProfile on `Engine.submit()` with 10,000 events (`PROFILING_RESULTS.md`).
- **Actual Observed Evidence**: Python function call overhead and dictionary operations consume $> 60\%$ of CPU time in `Engine.submit()`.
- **Consequence**: Single-core throughput cannot match pure C/C++ market data engines.
- **Recommended Remediation**: Deploy multi-process partitioning (`ChannelIngressSequencer` sharding) in Phase C of v5.1 to scale across multiple CPU cores.
- **Validation Test**: Multi-shard fleet benchmark.

---

## 3. Findings Summary Statistics

- **Total P0 Findings**: **0** (No critical data loss, silent corruptions, or safety vulnerabilities).
- **Total P1 Findings**: **0** (No release-blocking functional defects).
- **Total P2 Findings**: **2** (F-01: SPSC free-threaded safety; F-04: Kernel bypass simulation scope).
- **Total P3 Findings**: **3** (F-02: Test execution reporting; F-03: Benchmark timing overhead; F-05: Single-core GIL ceiling).
- **Total P4 Findings**: **0** (Minor items incorporated into roadmap).
- **Total Active Findings**: **5**.
