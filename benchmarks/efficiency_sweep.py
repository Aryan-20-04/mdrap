"""MDRAP Efficiency & Scaling Sweep (Phase 21).

Tests pipeline efficiency across expanding event scales:
1,000 -> 10,000 -> 100,000 events.
Monitors:
- Memory growth (RSS)
- GC behavior & collections
- Latency scaling (p50, p99)
- Throughput scaling limits
"""

import gc
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


def sweep_scale(event_counts: list[int] = [1_000, 10_000, 50_000]) -> list[dict[str, Any]]:
    import psutil
    proc = psutil.Process()
    results = []

    for n in event_counts:
        gc.collect()
        mem_start = proc.memory_info().rss
        gc_start = gc.get_count()

        store = Store(":memory:")
        pipeline = Pipeline(store=store)
        sim = FeedSimulator(SimulatorConfig(seed=42, num_events=n))

        t0 = time.perf_counter()
        for raw, _ in sim.generate():
            pipeline.process_one(raw)
        pipeline.finish()
        elapsed = time.perf_counter() - t0

        mem_end = proc.memory_info().rss
        gc_end = gc.get_count()

        eps = round(n / elapsed, 2)
        rss_mb = round(mem_end / (1024 * 1024), 2)
        summary = pipeline.metrics.summary()
        p50 = summary["e2e_latency_us"]["p50"]
        p99 = summary["e2e_latency_us"]["p99"]

        store.close()

        res = {
            "events": n,
            "elapsed_seconds": round(elapsed, 4),
            "throughput_eps": eps,
            "p50_latency_us": p50,
            "p99_latency_us": p99,
            "peak_rss_mb": rss_mb,
            "gc_delta": [gc_end[i] - gc_start[i] for i in range(3)],
        }
        results.append(res)
        print(f"Scale {n:>6} events: {eps:>8.1f} eps | p50: {p50:>6.1f}us | p99: {p99:>6.1f}us | RSS: {rss_mb:>5.1f}MB")

    return results


if __name__ == "__main__":
    scales = [1_000, 10_000, 50_000]
    res = sweep_scale(scales)
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    out_file = os.path.join(results_dir, "efficiency_sweep.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(f"\n[OK] Efficiency sweep complete. Saved to {out_file}")
