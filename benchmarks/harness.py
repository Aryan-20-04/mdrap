"""
MDRAP Standard Benchmark Harness.

Provides deterministic statistical measurement with warmup cycles,
percentiles (p50, p90, p99, p99.9) using time.perf_counter_ns,
median-of-medians, and interquartile range (IQR).
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable


def percentile(vals: list[float | int], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[int(f)] * (c - k) + s[int(c)] * (k - f))


def measure(fn: Callable[[], Any], runs: int = 7, warmup: int = 2) -> dict[str, Any]:
    """
    Execute `fn` across `warmup` discarded iterations and `runs` timed iterations.

    If `fn()` returns a list/tuple of numeric per-event/iteration latencies (in ns or us),
    percentiles are computed per run. If `fn()` returns a scalar or None, the end-to-end
    elapsed time (in ns) of `fn()` is recorded as the single run latency.
    """
    for _ in range(warmup):
        fn()

    run_summaries: list[dict[str, float]] = []
    run_p50s: list[float] = []
    run_p90s: list[float] = []
    run_p99s: list[float] = []
    run_p99_9s: list[float] = []
    run_totals_ns: list[int] = []

    for _ in range(runs):
        t0 = time.perf_counter_ns()
        res = fn()
        t1 = time.perf_counter_ns()
        elapsed_ns = t1 - t0
        run_totals_ns.append(elapsed_ns)

        if isinstance(res, (list, tuple)) and res and isinstance(res[0], (int, float)):
            samples = res
            p50 = percentile(samples, 50.0)
            p90 = percentile(samples, 90.0)
            p99 = percentile(samples, 99.0)
            p99_9 = percentile(samples, 99.9)
        elif isinstance(res, (int, float)):
            p50 = p90 = p99 = p99_9 = float(res)
        else:
            p50 = p90 = p99 = p99_9 = float(elapsed_ns)

        run_p50s.append(p50)
        run_p90s.append(p90)
        run_p99s.append(p99)
        run_p99_9s.append(p99_9)
        run_summaries.append({
            "elapsed_ns": elapsed_ns,
            "p50": p50,
            "p90": p90,
            "p99": p99,
            "p99_9": p99_9,
        })

    median_p50 = percentile(run_p50s, 50.0)
    q1 = percentile(run_p50s, 25.0)
    q3 = percentile(run_p50s, 75.0)
    iqr = q3 - q1

    return {
        "p50": median_p50,
        "p90": percentile(run_p90s, 50.0),
        "p99": percentile(run_p99s, 50.0),
        "p99_9": percentile(run_p99_9s, 50.0),
        "median_of_medians": median_p50,
        "iqr": iqr,
        "q1": q1,
        "q3": q3,
        "runs": run_summaries,
        "elapsed_ns_median": percentile(run_totals_ns, 50.0),
    }
