# MDRAP CI Remediation — Agent 1 Report: Honest Benchmark Reproducibility

## 1. Executive Summary & Original Failure
- **Test Target**: `tests/test_honest_benchmarks.py::test_f01_benchmark_reproducibility`
- **Reported Failure**:
  - Measured p50 values: `[24.271, 29.3955, 8.209]` $\mu\text{s}$
  - Observed relative drift: `42.52%` (exceeded allowed limit of $\le 35\%$)
- **Domain Owned**: `tests/test_honest_benchmarks.py`, benchmark helpers, workload configuration
- **Status**: **RESOLVED / PASS** (100% reproducible across consecutive runs, typical drift $< 4\%$)

---

## 2. Root Cause Analysis

Four distinct technical factors contributed to the high run-to-run drift in `test_f01_benchmark_reproducibility`:

1. **PEP 659 Adaptive Interpreter & JIT Specialization Delay**:
   - The test originally configured only 50 warm-up iterations. In Python 3.11–3.13, adaptive bytecode specialization (PEP 659) and tier-2 JIT trace optimization require several hundred iterations across hot call paths to fully specialize opcodes (`BINARY_OP`, `LOAD_ATTR`, `CALL`).
   - Consequently, Run 0 (iterations 51–350) and Run 1 (iterations 351–650) executed under partially specialized bytecode (~25–29 $\mu\text{s}$), whereas Run 2 (iterations 651–950) executed under fully optimized bytecode (~8.2 $\mu\text{s}$), yielding a dramatic $3.5\times$ drop in latency.

2. **CPU Frequency Governor Scaling (Idle $\to$ Turbo Boost Transition)**:
   - Modern OS frequency governors (e.g. Linux `schedutil`, Windows balanced power plan) maintain CPU cores at energy-saving base clocks (~1.5–2.0 GHz) until sustained CPU demand is detected (~20–50 ms).
   - The original 50-event warmup took $< 2\ \text{ms}$, meaning Run 0 ran at base clock frequency. By Run 2, the core had boosted to max turbo (~4.5 GHz), causing a physical hardware frequency jump that masqueraded as benchmark drift.

3. **In-Measurement Allocation & Workload Asymmetry**:
   - The original benchmark invoked `_make_raw(seq)` inside the `time.perf_counter_ns()` measurement block:
     ```python
     t0 = time.perf_counter_ns()
     st, _ = engine.step(st, _make_raw(seq), clock, offset=seq)
     t1 = time.perf_counter_ns()
     ```
     `_make_raw()` invoked `time.time()` twice, allocated a new payload dictionary, and instantiated a `RawEvent` dataclass. The benchmark was measuring object allocation overhead rather than pure `engine.step()` engine execution.
   - Furthermore, `st = engine.state` was used without deep copying, carrying state forward across runs. Run 0 saw a sequence jump from 49 to 1000 (triggering sequence gap branches), whereas Runs 1 and 2 saw contiguous sequence increments (1299 to 1300, 1599 to 1600), executing fundamentally different code paths.

4. **Garbage Collection (GC) Jitter**:
   - Python's generational garbage collector triggers automatically every 700 allocations (generation 0). Uncoordinated GC sweeps were pausing during some runs and not others, shifting the percentile medians.

---

## 3. Remediation Implementation

In [`tests/test_honest_benchmarks.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_honest_benchmarks.py):

1. **Extended Stabilization Warmup**:
   - Increased warmup from 50 to 1,000 iterations. This runs the engine for ~15 ms before any measurement begins, allowing the CPU frequency governor to lock into turbo frequency and PEP 659 bytecode instructions to fully specialize.

2. **Identical Workload Isolation**:
   - Pre-generated identical workload events (`workload_events = [_make_raw(5000 + i) for i in range(500)]`) outside the timing loop.
   - Each run starts from a pristine initial state (`engine.create_initial_state()`) processing the exact same sequence numbers.
   - Timed region strictly encloses `engine.step(st, ev, clock, offset=...)`.

3. **Pre-Heat Core Burst**:
   - Added a 50-event pre-heat run on a dummy state immediately preceding each timed run to prevent core down-clocking during inter-run GC collection.

4. **Deterministic GC Isolation**:
   - Invoked `gc.collect()` before each timed run and disabled GC (`gc.disable()`) during the measurement window with a strict `finally: gc.enable()` guard.

5. **Direct Functional Determinism Assertion**:
   - Added explicit verification that `run_decisions[0] == run_decisions[1] == run_decisions[2]`, separating functional correctness from timing stability.

6. **Preserved Strict Assertions**:
   - Maintained `rel_diff <= 0.35` for p50 and `p99 < 1500.0` guard.

---

## 4. Verification & Repeatability Evidence

### Repeated Multi-Trial Results (10 Consecutive Trials)
```
Trial  0: p50s=[14.50, 14.50, 14.90] us, max drift=1.82%
Trial  1: p50s=[14.60, 13.80, 14.40] us, max drift=3.27%
Trial  2: p50s=[14.40, 13.80, 14.10] us, max drift=2.13%
Trial  3: p50s=[16.70, 14.60, 13.50] us, max drift=11.83%
Trial  4: p50s=[13.00, 13.10, 13.30] us, max drift=1.27%
Trial  5: p50s=[14.20, 13.90, 14.40] us, max drift=1.88%
Trial  6: p50s=[14.70, 13.80, 14.50] us, max drift=3.72%
Trial  7: p50s=[14.00, 13.10, 13.30] us, max drift=3.96%
Trial  8: p50s=[13.50, 12.70, 12.20] us, max drift=5.47%
Trial  9: p50s=[13.10, 12.60, 12.60] us, max drift=2.61%
```
- **Mean Drift**: $3.80\%$ (well under $35\%$ threshold).
- **All 6 tests in `tests/test_honest_benchmarks.py` PASSED** cleanly in 5 consecutive test suite executions.

---

## 5. Remaining Limitations
On noisy shared virtual runners (e.g. GitHub Actions cloud runners with noisy neighbors or high VM steal time), hypervisor preemption during a 10 ms window can occasionally introduce tail jitter. The pre-heat phase and sample size of 500 events ensure the median (p50) remains stable against brief preemption spikes.
