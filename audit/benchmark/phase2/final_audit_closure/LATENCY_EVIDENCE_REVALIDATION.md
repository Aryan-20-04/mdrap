# MDRAP Phase 2.3 — Latency Evidence Revalidation Audit

**Audit Target:** End-to-End Latency Instrumentation, Reservoir Sampling, and Percentile Calculations  
**Evaluation Git Branch:** `main`  
**Evaluation HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Clean Release Reference:** `maintenance/architecture-pruning-v608-clean` @ `38ec051e0958c2199268a03afb54f6f24b89cd58`  
**Auditor:** Independent Quality & Technical Certification Directorate  
**Governance Policy:** STRICTLY READ-ONLY. NO PRODUCTION DEPLOYMENT. NO CAPITAL AT RISK.

---

## 1. Executive Summary

This forensic report audits the latency measurement instrumentation, statistical sampling algorithms, and percentile reporting implemented during the Phase 2.2 Two-Hour Reliability Soak benchmark. The audit evaluated source-code timestamp boundaries, clock domains, Vitter's Algorithm R implementation, and the physical distinction between median and tail latencies across 6,984,800 processed events.

The audit confirms that the **median latency of $476.60\text{ }\mu\text{s}$ is verified and strictly sub-millisecond**. However, generic claims that MDRAP exhibited a **"sub-millisecond latency profile throughout" are factually false and refuted**, as tail latencies systematically exceed 1.0 ms ($p_{90} = 1.05\text{ ms}$, $p_{99} = 3.04\text{ ms}$, $p_{99.9} = 10.04\text{ ms}$, and $\text{Max} = 99.88\text{ ms}$). Furthermore, timestamp analysis reveals that the recorded latency measures TCP transport and client-side receive time, excluding the daemon's internal normalization pipeline.

---

## 2. Latency Instrumentation & Clock-Domain Audit

### 2.1. Timestamp Boundaries in Source Code
Inspection of the measurement path reveals where timestamps were acquired:
1. **Producer / Broadcast Timestamp:** In `src/mdrap/service.py` (lines 867, 886):
   ```python
   t_broadcast = time.time()
   payload = {
       # ...
       "broadcast_ts": t_broadcast,
       "ingest_ts": raw.receive_timestamp,
       "proc_us": round(lat_us, 1),
   }
   ```
2. **Consumer / Receive Timestamp:** In `benchmarks/run_phase2_2h_soak.py` (lines 187, 222–224):
   ```python
   t_recv = time.time()
   # ...
   t_pub = evt.get("ts") or evt.get("broadcast_ts") or 0.0
   if t_pub > 0:
       lat = (t_recv - t_pub) * 1e6
   ```

### 2.2. Forensic Clock-Domain Findings
1. **System Wall Clock vs. Monotonic Clocks:** Both `t_broadcast` and `t_recv` queried `time.time()`, which is the system wall clock, **not a monotonic clock** (`time.perf_counter()` or `time.monotonic()`).
   - On Windows systems, `time.time()` is subject to NTP clock slews, time-zone adjustments, and operating system timer interrupt granularity (typically 0.5–1.0 ms unless high-resolution timer mode is forced).
   - In contrast, the daemon's internal processing latency (`proc_us`) used `time.perf_counter_ns()`, which is strictly monotonic.
2. **What Was Actually Measured:**
   - Because `t_pub` resolved to `broadcast_ts` (taken immediately before socket transmission), the metric `lat` represents **network socket transport, TCP stack transit, OS context switching, and client JSON parsing time**.
   - It **does NOT represent full end-to-end ingest-to-Observer latency**, because the time spent in gateway normalization, validation rules, depth updates, and BBO calculations (`proc_us`) occurred *before* `broadcast_ts`.
3. **Clock-Domain Skew:** Because both the daemon and the client ran inside the same process on localhost, cross-machine clock skew was eliminated, but wall-clock resolution jitter was present.

---

## 3. Mathematical Verification of Reservoir Sampling

To prevent unbounded memory growth over 7,200 seconds, the runner implemented a fixed-capacity reservoir sampler (`ReservoirSampler`, lines 185–225) based on **Vitter's Algorithm R** (1985).

### 3.1. Implementation Inspection
```python
class ReservoirSampler:
    def __init__(self, capacity: int = 50000):
        self.capacity = capacity
        self.samples: List[float] = []
        self.total_seen = 0

    def add(self, value: float) -> None:
        self.total_seen += 1
        if len(self.samples) < self.capacity:
            self.samples.append(value)
        else:
            j = random.randint(0, self.total_seen - 1)
            if j < self.capacity:
                self.samples[j] = value
```

### 3.2. Verification of Uniform Probability
Let $K = 50,000$ and total population $N = 6,984,800$:
1. For any element $i \le K$, the item is initially placed in the reservoir with probability 1.
2. For any subsequent element $i > K$, the probability of being selected is $\frac{K}{i}$.
3. For any item currently in the reservoir, the probability of remaining without being replaced is $1 - \frac{K}{i} \times \frac{1}{K} = 1 - \frac{1}{i} = \frac{i-1}{i}$.
4. By telescoping induction over all $i = K+1, \dots, N$:
   $$P(\text{selected and retained at } N) = 1 \times \prod_{i=K+1}^N \frac{i-1}{i} = \frac{K}{N}$$
   $$P = \frac{50,000}{6,984,800} \approx \mathbf{0.0071584 \quad (0.7158\%)}$$
   Every event across the entire two-hour run had an identical, unbiased probability of being included in the sample.

### 3.3. Sampling Limitations & Maximum Outlier Interpretation
- **Percentile Precision:** A uniform sample of 50,000 observations provides 500 samples in the top 1% ($p_{99}$) and 50 samples in the top 0.1% ($p_{99.9}$), ensuring high statistical confidence for percentiles up to $p_{99.9}$.
- **Global Maximum Limitation:** The reported maximum latency of $99,882.13\text{ }\mu\text{s}$ (~99.88 ms) is the maximum *within the 50,000-sample reservoir*.
- In a population of 6.98 million events, rare microsecond outliers that did not land in the 0.7158% sample could theoretically have been higher.
- **Audit Rule:** 50,000 samples **cannot and do not prove** that every possible worst-case tail event was observed.

---

## 4. Percentile Calculation & Refutation of "All Sub-ms" Claims

### 4.1. The Empirical Latency Distribution
The final sorted reservoir yielded the following distribution:

| Metric | Measured Value (µs) | Equivalent (ms) | Target Standard | Status | Audit Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Median ($p_{50}$)** | 476.60 µs | 0.477 ms | $< 1,000\text{ }\mu\text{s}$ | **Sub-millisecond** | **VERIFIED** |
| **90th Percentile ($p_{90}$)** | 1,052.86 µs | 1.053 ms | $< 1,000\text{ }\mu\text{s}$ | **Exceeds 1 ms** | **VERIFIED** |
| **95th Percentile ($p_{95}$)** | 1,459.60 µs | 1.460 ms | $< 1,000\text{ }\mu\text{s}$ | **Exceeds 1 ms** | **VERIFIED** |
| **99th Percentile ($p_{99}$)** | 3,037.69 µs | 3.038 ms | $< 1,000\text{ }\mu\text{s}$ | **3.04x above 1 ms** | **VERIFIED** |
| **99.9th Percentile ($p_{99.9}$)**| 10,044.81 µs | 10.045 ms | $< 1,000\text{ }\mu\text{s}$ | **10.05x above 1 ms** | **VERIFIED** |
| **Maximum Sampled** | 99,882.13 µs | 99.882 ms | N/A | **99.88x above 1 ms** | **VERIFIED** |

### 4.2. Refutation of Universal Sub-Millisecond Claims
- **Marketing Claim:** *"Sub-millisecond latency profile throughout."*
- **Audit Refutation:** This claim is **FAILED / REFUTED**. Only the median ($p_{50}$) is sub-millisecond. The 90th, 95th, 99th, and 99.9th percentiles all exceed 1.0 ms. Downstream trading infrastructure cannot assume sub-millisecond delivery under tail conditions.

---

## 5. Temporal Stability: First-10 vs. Final-10 Window Comparison

To determine whether the pipeline suffered queue buildup, socket starvation, or scheduling degradation over two hours, the runner evaluated the initial 10 windows (Minutes 1–10) against the final 10 windows (Minutes 111–120):

```
Initial 10-Minute Windows (Min 1 - 10):
  Average p50: 570.99 µs
  Average p99: 1,753.83 µs

Final 10-Minute Windows (Min 111 - 120):
  Average p50: 500.20 µs
  Average p99: 1,835.65 µs

Temporal Drift:
  Δ p50: -70.79 µs  (-12.40% drift - improvement via warm-up)
  Δ p99: +81.82 µs  (+4.66% drift - stable within standard jitter)
```

### 5.1. What These Measurements Establish
1. **No Queue Buildup:** Median latency did not grow over time; it actually improved by $70.79\text{ }\mu\text{s}$ as CPython bytecodes and OS caches reached steady-state warmth.
2. **Stable Tail Bounds:** The 99th percentile varied by only +4.66% over 7,200 seconds, confirming that socket buffers drained consistently without memory-induced stalls.

### 5.2. What These Measurements Do NOT Establish
1. They do **not** establish performance under multi-client network fan-out (only a single local TCP connection was tested).
2. They do **not** establish latency under high-contention lock conditions or bursting above 1,000 EPS.

---

## 6. Audit Verdicts

1. **Overall Median ($p_{50} = 476.60\text{ }\mu\text{s}$):** **VERIFIED** (Sub-millisecond median established).
2. **Tail Percentiles ($p_{90} - p_{99.9} = 1.05 - 10.05\text{ ms}$):** **VERIFIED**.
3. **"Universal Sub-Millisecond Profile" Claim:** **FAILED / REFUTED**.
4. **Vitter's Algorithm R Mathematical Rigor:** **VERIFIED** ($K=50,000$, uniform $P=0.7158\%$).
5. **Two-Hour Temporal Stability (No Degradation):** **VERIFIED**.
6. **Clock Domain Instrumentation (Wall Clock Limitation):** **VERIFIED**.
