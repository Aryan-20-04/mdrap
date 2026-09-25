"""
Benchmark Comparison: V1 (Pure Python) vs V4 (Native C Hot Path).

Runs both pipelines across 7 runs (2 warmup) at 100,000 events (seed=42),
measuring throughput (eps) and processing latency (p50, IQR) via benchmarks.harness.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from harness import measure
from simulator import FeedSimulator, SimulatorConfig
from storage import Store
from pipeline import Pipeline
from quality import QualityEngine
from fastpath import FastQualityEngine


def benchmark_tier(tier: str, events: int = 100_000, seed: int = 42) -> dict:
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=events))
    raw_events = [raw for raw, _ in sim.generate()]

    def run():
        store = Store(":memory:")
        quality = FastQualityEngine() if tier == "v4" else QualityEngine()
        pipe = Pipeline(store, quality=quality)
        for raw in raw_events:
            pipe.process_one(raw)
        pipe.finish()
        summary = pipe.metrics.summary()
        p50 = summary["processing_latency_us"]["p50"]
        eps = pipe.metrics.throughput()
        store.close()
        return {"p50": p50, "eps": eps}

    # Run harness on p50
    p50_samples = []
    eps_samples = []

    def run_wrapper():
        res = run()
        p50_samples.append(res["p50"])
        eps_samples.append(res["eps"])
        return res["p50"]

    harness_res = measure(run_wrapper, runs=7, warmup=2)
    # The last 7 entries correspond to the 7 timed runs
    timed_eps = eps_samples[-7:]
    sorted_eps = sorted(timed_eps)
    median_eps = sorted_eps[len(sorted_eps) // 2]

    harness_res["median_eps"] = round(median_eps, 1)
    harness_res["tier"] = tier.upper()
    return harness_res


def main():
    parser = argparse.ArgumentParser(description="Compare V1 vs V4 performance")
    parser.add_argument(
        "--events", type=int, default=100_000, help="Event count (default 100,000)"
    )
    parser.add_argument("--seed", type=int, default=42, help="Seed (default 42)")
    args = parser.parse_args()

    print(
        f"[*] Running V1 vs V4 benchmark ({args.events:,} events, 7 runs, 2 warmup)..."
    )
    print("    Benchmarking V1 (Pure Python)...")
    v1 = benchmark_tier("v1", events=args.events, seed=args.seed)
    print(
        f"    -> V1 p50: {v1['p50']:.2f} µs (IQR: {v1['iqr']:.2f} µs) | {v1['median_eps']:,.1f} eps"
    )

    print("    Benchmarking V4 (Native C Hot Path)...")
    v4 = benchmark_tier("v4", events=args.events, seed=args.seed)
    print(
        f"    -> V4 p50: {v4['p50']:.2f} µs (IQR: {v4['iqr']:.2f} µs) | {v4['median_eps']:,.1f} eps"
    )

    print("\n" + "=" * 70)
    print("V1 Baseline vs V4 Native C Comparison (7 timed runs, 2 warmup)")
    print("=" * 70)
    print(f"{'Metric':<30} | {'V1 (Pure Python)':<18} | {'V4 (Native C)':<18}")
    print("-" * 70)
    print(
        f"{'Processing Latency p50':<30} | {v1['p50']:>14.2f} µs | {v4['p50']:>14.2f} µs"
    )
    print(f"{'p50 IQR (Spread)':<30} | {v1['iqr']:>14.2f} µs | {v4['iqr']:>14.2f} µs")
    print(
        f"{'p50 Q1 - Q3 Range':<30} | {v1['q1']:.2f} - {v1['q3']:.2f} µs  | {v4['q1']:.2f} - {v4['q3']:.2f} µs"
    )
    print(
        f"{'Throughput (median)':<30} | {v1['median_eps']:>14,.1f} eps | {v4['median_eps']:>14,.1f} eps"
    )
    print("-" * 70)

    p50_wins = v4["p50"] < v1["p50"]
    eps_wins = v4["median_eps"] > v1["median_eps"]
    non_overlapping = v4["q3"] < v1["q1"]

    print(f"V4 beats V1 on p50:        {'YES' if p50_wins else 'NO'}")
    print(f"V4 beats V1 on throughput: {'YES' if eps_wins else 'NO'}")
    print(f"Non-overlapping IQRs:      {'YES' if non_overlapping else 'OVERLAPPING'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
