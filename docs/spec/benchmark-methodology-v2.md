# MDRAP Benchmark Methodology Specification (v2)

- **Status**: FROZEN (Milestone M1 Contract)
- **Document Version**: 2.0.0
- **Scope**: Rigorous, reproducible latency and throughput evaluation across the native core and hybrid pipeline.

---

## 1. Principles of Honest Financial Infrastructure Benchmarking

High-frequency market data infrastructure requires measurement rigor that avoids common benchmarking traps:
1. **Coordinated Omission**: Traditional closed-loop benchmarks measure service time rather than latency experienced by the market. When a pipeline stalls, closed-loop generators pause, artificially masking the queue buildup and tail latency.
2. **Tail Latency Dominance**: Averages and p50s hide execution risk. Financial infrastructure SLA is decided at **p99, p99.9, and maximum latency**.
3. **No Syscalls or Allocations in the Hot Loop**: Steady-state measurements must demonstrate zero dynamic heap allocations (`malloc`/`free`) and zero kernel context switches / syscalls.
4. **Hardware Grounding**: Numbers measured on shared public cloud virtual machines with CPU throttling, noisy neighbors, and variable hypervisor scheduling must be clearly labeled indicative; headline latency figures require dedicated bare metal.

---

## 2. Load Generation Architecture

### 2.1 Open-Loop Traffic Generation
Benchmarks must use an **open-loop schedule** where events are emitted at pre-determined arrival intervals $t_i$:
$$t_i = t_0 + \sum_{k=1}^{i} \Delta t_k$$
where $\Delta t_k$ follows either a deterministic line-rate interval (e.g. constant 1,000,000 msgs/sec) or a Poisson burst distribution modeling market open / economic releases.

If the consumer is stalled at time $t_i$, the arrival timestamp is recorded, and the delay is explicitly accumulated in the latency histogram (coordinated omission correction).

---

## 3. High-Dynamic-Range (HDR) Histograms

Latency distributions must be captured using HDR histogram structures with high dynamic range and constant relative error:
- **Unit**: Nanoseconds (`ns`)
- **Range**: 1 nanosecond to 30 seconds ($3 \times 10^{10}\text{ ns}$)
- **Significant Digits**: 3 decimal digits of precision across all buckets
- **Required Metrics**:
  - `p50` (Median)
  - `p90`
  - `p99` (Hot percentile)
  - `p99.9` (Tail / 1 in 1,000 events)
  - `p99.99` (Extreme tail / 1 in 10,000 events)
  - `Max` (Worst-case single event observed)

---

## 4. High-Resolution Clock Sources

### 4.1 Invariant TSC (`rdtsc`)
On modern x86_64 processors, the Time Stamp Counter (TSC) increments at a constant frequency independent of CPU core frequency scaling or sleep states (Invariant TSC).
- Read via compiler intrinsic: `__rdtsc()` or `__rdtscp(&aux)`.
- Eliminates the 15–30 ns overhead of calling `clock_gettime(CLOCK_MONOTONIC_RAW)` via vDSO.
- Calibrated at startup against monotonic time over 1,000,000 cycles to determine nanoseconds per tick:
  $$\text{ns\_per\_tick} = \frac{\text{elapsed\_ns}}{\text{elapsed\_tsc}}$$

### 4.2 Linux vDSO Fallback
If invariant TSC is unsupported (e.g. nested virtualization), fall back to:
```c
clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
```

---

## 5. System Tuning and Environment Preconditions

To produce credible benchmark measurements, host environments must satisfy:
1. **Core Isolation**: Hot-path threads pinned to dedicated cores isolated from OS scheduling (`isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3`).
2. **CPU Governor**: Set to `performance` mode (`cpupower frequency-set -g performance`).
3. **C-States**: Hardware C-states disabled or restricted to C0/C1 (`intel_idle.max_cstate=0 processor.max_cstate=0`).
4. **Memory Locking**: Pre-fault and lock all memory pages with `mlockall(MCL_CURRENT | MCL_FUTURE)` to prevent minor page faults in the hot loop.
5. **Huge Pages**: Transparent Huge Pages (THP) disabled for latency consistency (`echo never > /sys/kernel/mm/transparent_hugepage/enabled`) or pre-allocated 2MB huge pages used for shared memory arenas.
