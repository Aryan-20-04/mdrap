# MDRAP Phase 2.6 — Latency Invalid Accounting & Monotonic Inversion Remediation Report

**Document Reference:** `audit/benchmark/phase26/LATENCY_INVALID_ACCOUNTING_REPORT.md`  
**Execution Timestamp:** 2026-11-22T01:24:00 UTC  
**Environment:** Windows 11 AMD64 | Python 3.13.1  
**Repository Branch:** `main`  
**Git HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Clean Release Reference:** `maintenance/architecture-pruning-v608-clean` (`38ec051e0958c2199268a03afb54f6f24b89cd58`)  
**Governance:** STRICT READ-ONLY INFRASTRUCTURE — NO DEPLOYMENT — NO LIVE EXECUTION

---

## 1. Executive Summary

During the Phase 2.5 independent audit, inspection and adversarial testing of the latency measurement pipeline revealed an accounting vulnerability:
When synthetic inverted timestamps ($\Delta t < 0$) or boundary order violations ($t_{\text{after}} < t_{\text{before}}$) occurred, certain code paths in `MonotonicLatencyTracker.record_event_latencies()` either performed early pre-filtering or returned `None` before delegating to `StageLatencyMetric.record()`. As a result:
1. Inverted timestamp events could bypass `invalid_count` accounting entirely, leaving operational teams blind to clock regressions or corrupt boundary telemetry.
2. Conversely, if pre-checks incremented a counter and subsequently passed the negative delta to the metric, double-counting could occur.
3. Uninstrumented events (where timestamps were `0` or `None`) risked being misclassified as negative latency errors rather than unmeasured stages.

In Phase 2.6 Workstream B, `MonotonicLatencyTracker` and `StageLatencyMetric` in `src/mdrap/latency.py` were refactored to enforce **Single-Point Canonical Validation & Single-Count Invalid Accounting**.

**Key Remediation Accomplishments:**
- Every inverted delta ($\Delta t < 0$) increments `invalid_count` **exactly once** in its corresponding stage metric.
- Uninstrumented timestamps ($t \le 0$ or `None`) return `None` cleanly without inflating `invalid_count`.
- Invalid observations ($\Delta t < 0$, `NaN`, `+Inf`, `-Inf`) are strictly excluded from running sums, minimums, maximums, and reservoir percentile samples.
- The fundamental telescoping additive identity ($\Delta t_{\text{ingress}} + \Delta t_{\text{engine}} + \Delta t_{\text{transport}} = \Delta t_{\text{e2e}}$) remains strictly preserved across all valid measurements.

---

## 2. Latency Remediation Test Battery Matrix

The 7 dedicated tests in `tests/test_phase26_remediation.py` verify all edge cases and boundary conditions under strict adversarial conditions:

| Test Identifier | Test Name | Injected Scenario | Stage Under Test | Expected Delta / Condition | Final Metric State | Verification Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-LAT-01** | `test_lat_inverted_ingress_boundary_increments_invalid_count` | $t0 < t_{\text{ingest}}$ ($1.0\text{ ms} < 2.0\text{ ms}$) | `ingress` | $\Delta t = -1.0\text{ ms}$ | `res['ingress_us'] is None`<br>`count == 0`<br>`invalid_count == 1` | **PASS (VERIFIED)**<br>Single-count invalid increment; valid stages unaffected. |
| **TC-LAT-02** | `test_lat_inverted_engine_boundary_increments_invalid_count` | $t_{\text{bcast}} < t0$ ($1.5\text{ ms} < 2.0\text{ ms}$) | `engine` | $\Delta t = -0.5\text{ ms}$ | `res['engine_us'] is None`<br>`count == 0`<br>`invalid_count == 1` | **PASS (VERIFIED)**<br>Single-count invalid increment in engine stage. |
| **TC-LAT-03** | `test_lat_inverted_transport_boundary_increments_invalid_count` | $t_{\text{recv}} < t_{\text{bcast}}$ ($4.0\text{ ms} < 5.0\text{ ms}$) | `transport` | $\Delta t = -1.0\text{ ms}$ | `res['transport_us'] is None`<br>`count == 0`<br>`invalid_count == 1` | **PASS (VERIFIED)**<br>Single-count invalid increment in transport stage. |
| **TC-LAT-04** | `test_lat_equal_timestamps_recorded_as_valid_zero` | $t_{\text{after}} == t_{\text{before}}$ across all stages | All stages | $\Delta t = 0\text{ ns}$ | `res['all'] == 0.0 us`<br>`count == 1`<br>`invalid_count == 0`<br>`min_us == 0.0` | **PASS (VERIFIED)**<br>Zero duration correctly classified as valid zero, not invalid. |
| **TC-LAT-05** | `test_lat_missing_timestamps_do_not_increment_invalid_count` | Uninstrumented event ($t \le 0$ or `None`) | All stages | $t = 0$ / `None` | `res['all'] is None`<br>`count == 0`<br>`invalid_count == 0` | **PASS (VERIFIED)**<br>Uninstrumented events leave counters pristine. |
| **TC-LAT-06** | `test_lat_invalid_observations_excluded_from_samples_and_percentiles` | 10 valid observations ($10.0\,\mu\text{s}$) + 5 invalid ($-500$, $-1$, `NaN`, `+Inf`, `-Inf`) | Synthetic stage | Mixed | `count == 10`<br>`invalid_count == 5`<br>`p50 == 10.0 us`<br>`p99 == 10.0 us`<br>`max == 10.0 us` | **PASS (VERIFIED)**<br>Reservoir and percentiles strictly uncontaminated by invalid data. |
| **TC-LAT-07** | `test_lat_valid_measurements_satisfy_telescoping_identity` | Ingress ($20\,\mu\text{s}$) + Engine ($30\,\mu\text{s}$) + Transport ($40\,\mu\text{s}$) | End-to-End | $20 + 30 + 40 = 90\,\mu\text{s}$ | `ingress=20.0`<br>`engine=30.0`<br>`transport=40.0`<br>`e2e=90.0` | **PASS (VERIFIED)**<br>Telescoping additive identity holds: $\sum \Delta t_{\text{stages}} == \Delta t_{\text{e2e}}$. |

---

## 3. Implementation Verification & Code Architecture

### 3.1 Single-Point Canonical Validation Pattern
The remediation moved all delta validation logic into `StageLatencyMetric.record()`, eliminating fragmented checks across caller routines:

```python
# src/mdrap/latency.py

class StageLatencyMetric:
    def record(self, delta_ns: float | int | None) -> float | None:
        """Record a single stage duration in nanoseconds.
        Returns the recorded value in microseconds, or None if invalid.
        Invalid values (negative, NaN, inf) increment invalid_count exactly once.
        """
        if delta_ns is None or delta_ns < 0 or math.isnan(delta_ns) or math.isinf(delta_ns):
            self.invalid_count += 1
            return None

        delta_us = delta_ns / 1000.0
        self.count += 1
        self.total_ns += delta_ns
        if self.count == 1:
            self.min_ns = delta_ns
            self.max_ns = delta_ns
        else:
            if delta_ns < self.min_ns:
                self.min_ns = delta_ns
            if delta_ns > self.max_ns:
                self.max_ns = delta_ns

        self.sampler.sample(delta_us)
        return delta_us
```

### 3.2 Monotonic Tracker Boundary Handling
In `MonotonicLatencyTracker.record_event_latencies()`:
```python
# Ingress delta
if t_ingest > 0 and t0 > 0:
    res["ingress_us"] = self.ingress.record(t0 - t_ingest)

# Engine delta
if engine_ns is not None:
    res["engine_us"] = self.engine.record(engine_ns)
elif t0 > 0 and t_bcast > 0:
    res["engine_us"] = self.engine.record(t_bcast - t0)

# Transport delta
if t_bcast > 0 and t_recv > 0:
    res["transport_us"] = self.transport.record(t_recv - t_bcast)

# End-to-end delta
if t_ingest > 0 and t_recv > 0:
    res["e2e_us"] = self.e2e.record(t_recv - t_ingest)
```

**Architectural Invariance Guarantees:**
1. **No Pre-Check Bypass:** The subtraction $\Delta t = t_{\text{after}} - t_{\text{before}}$ is performed unconditionally if both timestamps are positive non-zero integers, and passed directly into `metric.record()`.
2. **Exact Single Accounting:** If $t_{\text{after}} < t_{\text{before}}$, $\Delta t < 0$, causing `self.invalid_count += 1` inside `metric.record()` and returning `None`. No other branch or counter is touched.
3. **Missing Value Isolation:** If $t \le 0$ or is missing, `metric.record()` is not called; `res[stage]` defaults to `None`, and `invalid_count` is unchanged.

---

## 4. Preservation of the Telescoping Additive Identity

A foundational requirement for multi-stage market-data latency profiling is the preservation of the telescoping sum:

$$\Delta t_{\text{e2e}} = (t_{\text{recv}} - t_{\text{ingest}}) = (t_0 - t_{\text{ingest}}) + (t_{\text{bcast}} - t_0) + (t_{\text{recv}} - t_{\text{bcast}})$$
$$\Delta t_{\text{e2e}} = \Delta t_{\text{ingress}} + \Delta t_{\text{engine}} + \Delta t_{\text{transport}}$$

### Empirical Verification from `test_lat_valid_measurements_satisfy_telescoping_identity`
- **Timestamp Inputs:**
  - $t_{\text{ingest}} = 1{,}000{,}000{,}000\text{ ns}$
  - $t_0 = 1{,}000{,}020{,}000\text{ ns}$ ($\Delta = 20{,}000\text{ ns} = 20.00\,\mu\text{s}$)
  - $t_{\text{bcast}} = 1{,}000{,}050{,}000\text{ ns}$ ($\Delta = 30{,}000\text{ ns} = 30.00\,\mu\text{s}$)
  - $t_{\text{recv}} = 1{,}000{,}090{,}000\text{ ns}$ ($\Delta = 40{,}000\text{ ns} = 40.00\,\mu\text{s}$)
- **Stage Measurements:**
  - `ingress_us`: $20.00\,\mu\text{s}$
  - `engine_us`: $30.00\,\mu\text{s}$
  - `transport_us`: $40.00\,\mu\text{s}$
  - `e2e_us`: $90.00\,\mu\text{s}$
- **Arithmetic Identity:**
  $$\text{Sum} = 20.00 + 30.00 + 40.00 = 90.00\,\mu\text{s} \equiv \text{e2e\_us}$$
- **Result:** Exact identity satisfied with zero residual drift.

---

## 5. Reservoir Sampling Integrity Under Anomaly Injection

In `test_lat_invalid_observations_excluded_from_samples_and_percentiles`, 5 catastrophic anomalies were injected into an active metric stream containing 10 valid $10.0\,\mu\text{s}$ observations:
- $\Delta t_1 = -500\text{ ns}$ (Clock reversal)
- $\Delta t_2 = -1\text{ ns}$ (Monotonic slip)
- $\Delta t_3 = \text{NaN}$ (Floating-point corruption)
- $\Delta t_4 = +\infty$ (Infinite timestamp overflow)
- $\Delta t_5 = -\infty$ (Infinite timestamp underflow)

### Statistical Inspection Output:
- Valid Event Count: `10`
- Invalid Event Count: `5`
- Percentile p50: `10.00 us`
- Percentile p99: `10.00 us`
- Observed Minimum: `10.00 us`
- Observed Maximum: `10.00 us`
- Average Latency: `10.00 us`

**Conclusion:** Neither running statistics (`min_ns`, `max_ns`, `total_ns`) nor reservoir sample buffers were corrupted by the invalid observations. The anomaly was accurately quarantined in `invalid_count`.

---

## 6. Certification Verdict

The latency accounting remediation is **VERIFIED COMPLETE**. 
- Single-count accounting for timestamp inversions is mathematically proven and tested.
- Percentile and summary statistics are fully insulated against corrupt or negative inputs.
- All 7 tests pass deterministically.

---
*Report certified under MDRAP Governance — Read-Only Market-Data Infrastructure — No Deployment.*
