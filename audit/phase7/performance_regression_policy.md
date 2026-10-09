# MDRAP Phase 7 — Performance Regression Policy & Quality Gate Budgets

## 1. Executive Summary & Policy Contract
To prevent incremental software changes from silently introducing latency regressions or throughput degradation, MDRAP enforces an automated **Performance Regression Budget Gate**.

Any PR or commit that degrades baseline throughput by $> 20\%$ or increases tail latency ($p99$) by $> 30\%$ is automatically blocked from merge.

---

## 2. Formal Regression Budgets (Profile A: 20k Events)

| Metric | Target Baseline | Warning Threshold (+15%) | Hard CI Blocking Gate (+30%) | Production SLA Breach |
| :--- | :--- | :--- | :--- | :--- |
| **Sustained Throughput** | $\ge 20,000\text{ eps}$ | $< 17,000\text{ eps}$ | $< 15,000\text{ eps}$ | $< 5,000\text{ eps}$ |
| **p50 Latency (Median)**| $\le 10.0\text{ \mu s}$ | $> 12.0\text{ \mu s}$ | $> 15.0\text{ \mu s}$ | $> 25.0\text{ \mu s}$ |
| **p95 Latency** | $\le 25.0\text{ \mu s}$ | $> 30.0\text{ \mu s}$ | $> 40.0\text{ \mu s}$ | $> 100.0\text{ \mu s}$ |
| **p99 Latency (Tail)** | $\le 50.0\text{ \mu s}$ | $> 65.0\text{ \mu s}$ | $> 80.0\text{ \mu s}$ | $> 200.0\text{ \mu s}$ |
| **p99.9 Latency** | $\le 800.0\text{ \mu s}$ | $> 1,000.0\text{ \mu s}$ | $> 1,500.0\text{ \mu s}$ | $> 5,000.0\text{ \mu s}$ |
| **Memory Delta (20k ev)**| $\le 6.0\text{ MB}$ | $> 8.0\text{ MB}$ | $> 12.0\text{ MB}$ | Active leak $> 10\text{ MB/hr}$ |
| **Queue Dwell Time** | $\le 15.0\text{ \mu s}$ | $> 25.0\text{ \mu s}$ | $> 50.0\text{ \mu s}$ | $> 250.0\text{ \mu s}$ |

---

## 3. Enforcement & Verification Runbook

Automated assertions in the continuous quality gate script ([`scripts/run_phase7_quality_gates.py`](scripts/run_phase7_quality_gates.py)):

```python
def check_performance_budget(measured: dict, baseline: dict):
    eps_ratio = measured["throughput_eps"] / baseline["throughput_eps"]
    assert eps_ratio >= 0.80, (
        f"Throughput regression: {measured['throughput_eps']} eps is "
        f"{round((1 - eps_ratio) * 100, 1)}% below baseline"
    )

    p99_ratio = measured["latency_p99_us"] / baseline["latency_p99_us"]
    assert p99_ratio <= 1.30, (
        f"Tail latency regression: {measured['latency_p99_us']} µs is "
        f"{round((p99_ratio - 1) * 100, 1)}% above baseline budget"
    )
```
