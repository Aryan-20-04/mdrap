"""
MDRAP 6.0.7 — 7-Run Alternating Benchmark Reproducibility Suite.

Executes at least 7 alternating benchmark runs per revision:
- Revision A: Baseline (Pure-Python Fallback `--no-fastpath`)
- Revision B: Hardened (Native C Fastpath `--fastpath`)

Measures:
- Throughput (events/sec)
- Elapsed Wall Time (s)
- Processing Latency: p50, p95, p99, Max (μs)
- Stage breakdown (ingest, quality, reconcile, enqueue)
- Inter-run variability (Standard Deviation, Coefficient of Variation CV%)
"""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import time
from typing import Any

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def run_single_benchmark(use_fastpath: bool, seed: int = 42, events: int = 50000) -> dict[str, Any]:
    """Execute cli.py benchmark and parse resulting JSON."""
    label = "hardened" if use_fastpath else "baseline"
    cmd = [
        sys.executable,
        "cli.py",
        "benchmark",
        "--events",
        str(events),
        "--seed",
        str(seed),
        "--label",
        label,
        "--json",
    ]
    if not use_fastpath:
        cmd.append("--no-fastpath")
    else:
        cmd.append("--fastpath")

    t0 = time.perf_counter()
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    wall_duration = time.perf_counter() - t0

    # Parse stdout JSON (ignoring leading/trailing text lines)
    stdout = res.stdout.strip()
    json_start = stdout.find("{")
    if json_start < 0:
        raise ValueError(f"No JSON in benchmark output: {stdout}")
    data = json.loads(stdout[json_start:])

    perf = data.get("performance", {})
    proc_lat = perf.get("processing_latency_us", {})
    e2e_lat = perf.get("e2e_latency_us", {})
    stages = perf.get("stages_us", {})

    return {
        "label": label,
        "wall_duration_s": round(wall_duration, 4),
        "benchmark_elapsed_s": round(perf.get("elapsed_s", 0.0), 4),
        "throughput_eps": round(perf.get("throughput_eps", 0.0), 1),
        "proc_p50_us": proc_lat.get("p50", 0.0),
        "proc_p95_us": proc_lat.get("p95", 0.0),
        "proc_p99_us": proc_lat.get("p99", 0.0),
        "proc_max_us": proc_lat.get("max", 0.0),
        "e2e_p50_us": e2e_lat.get("p50", 0.0),
        "e2e_p95_us": e2e_lat.get("p95", 0.0),
        "e2e_p99_us": e2e_lat.get("p99", 0.0),
        "ingest_p50_us": stages.get("ingest_normalize", {}).get("p50", 0.0),
        "quality_p50_us": stages.get("quality_evaluate", {}).get("p50", 0.0),
        "reconcile_p50_us": stages.get("reconcile_analytics", {}).get("p50", 0.0),
        "enqueue_p50_us": stages.get("enqueue_storage", {}).get("p50", 0.0),
        "max_rss_mb": perf.get("max_rss_mb", 0.0),
    }


def compute_series_stats(series: list[float]) -> dict[str, float]:
    """Compute median, mean, p95, p99, stdev, and CV%."""
    if not series:
        return {}
    med = statistics.median(series)
    mean = statistics.mean(series)
    stdev = statistics.stdev(series) if len(series) > 1 else 0.0
    cv_pct = (stdev / mean * 100.0) if mean > 0 else 0.0
    p95 = statistics.quantiles(series, n=20)[18] if len(series) >= 20 else max(series)
    p99 = statistics.quantiles(series, n=100)[98] if len(series) >= 100 else max(series)

    return {
        "median": round(med, 2),
        "mean": round(mean, 2),
        "p95": round(p95, 2),
        "p99": round(p99, 2),
        "min": round(min(series), 2),
        "max": round(max(series), 2),
        "stdev": round(stdev, 2),
        "cv_pct": round(cv_pct, 2),
    }


def run_alternating_benchmarks(num_runs: int = 7, events: int = 50000) -> dict[str, Any]:
    """Execute alternating runs: Baseline (Pure-Python), Hardened (Native C)."""
    baseline_runs: list[dict[str, Any]] = []
    hardened_runs: list[dict[str, Any]] = []

    print(f"\n================================================================================")
    print(f"MDRAP 6.0.7: Executing {num_runs} Alternating Benchmark Runs per Revision ({events} events each)")
    print(f"================================================================================\n")

    for i in range(1, num_runs + 1):
        print(f"--- Alternating Pair {i}/{num_runs} ---")

        # 1. Run Baseline (Pure-Python fallback)
        print(f"  [Run {i}A] Baseline (Pure-Python)... ", end="", flush=True)
        res_b = run_single_benchmark(use_fastpath=False, seed=42, events=events)
        baseline_runs.append(res_b)
        print(f"Throughput: {res_b['throughput_eps']:,.1f} eps | p50: {res_b['proc_p50_us']} μs | p99: {res_b['proc_p99_us']} μs | Elapsed: {res_b['benchmark_elapsed_s']} s")

        # 2. Run Hardened (Native C fastpath)
        print(f"  [Run {i}B] Hardened (Native C)...   ", end="", flush=True)
        res_h = run_single_benchmark(use_fastpath=True, seed=42, events=events)
        hardened_runs.append(res_h)
        print(f"Throughput: {res_h['throughput_eps']:,.1f} eps | p50: {res_h['proc_p50_us']} μs | p99: {res_h['proc_p99_us']} μs | Elapsed: {res_h['benchmark_elapsed_s']} s")

    # Aggregate metric series
    def extract(runs: list[dict[str, Any]], key: str) -> list[float]:
        return [r[key] for r in runs if key in r]

    summary = {
        "num_runs": num_runs,
        "events_per_run": events,
        "baseline": {
            "runs": baseline_runs,
            "throughput_eps": compute_series_stats(extract(baseline_runs, "throughput_eps")),
            "elapsed_s": compute_series_stats(extract(baseline_runs, "benchmark_elapsed_s")),
            "proc_p50_us": compute_series_stats(extract(baseline_runs, "proc_p50_us")),
            "proc_p95_us": compute_series_stats(extract(baseline_runs, "proc_p95_us")),
            "proc_p99_us": compute_series_stats(extract(baseline_runs, "proc_p99_us")),
            "proc_max_us": compute_series_stats(extract(baseline_runs, "proc_max_us")),
            "ingest_p50_us": compute_series_stats(extract(baseline_runs, "ingest_p50_us")),
            "quality_p50_us": compute_series_stats(extract(baseline_runs, "quality_p50_us")),
            "reconcile_p50_us": compute_series_stats(extract(baseline_runs, "reconcile_p50_us")),
            "enqueue_p50_us": compute_series_stats(extract(baseline_runs, "enqueue_p50_us")),
            "max_rss_mb": compute_series_stats(extract(baseline_runs, "max_rss_mb")),
        },
        "hardened": {
            "runs": hardened_runs,
            "throughput_eps": compute_series_stats(extract(hardened_runs, "throughput_eps")),
            "elapsed_s": compute_series_stats(extract(hardened_runs, "benchmark_elapsed_s")),
            "proc_p50_us": compute_series_stats(extract(hardened_runs, "proc_p50_us")),
            "proc_p95_us": compute_series_stats(extract(hardened_runs, "proc_p95_us")),
            "proc_p99_us": compute_series_stats(extract(hardened_runs, "proc_p99_us")),
            "proc_max_us": compute_series_stats(extract(hardened_runs, "proc_max_us")),
            "ingest_p50_us": compute_series_stats(extract(hardened_runs, "ingest_p50_us")),
            "quality_p50_us": compute_series_stats(extract(hardened_runs, "quality_p50_us")),
            "reconcile_p50_us": compute_series_stats(extract(hardened_runs, "reconcile_p50_us")),
            "enqueue_p50_us": compute_series_stats(extract(hardened_runs, "enqueue_p50_us")),
            "max_rss_mb": compute_series_stats(extract(hardened_runs, "max_rss_mb")),
        },
    }

    # Print summary comparison table
    b_tp = summary["baseline"]["throughput_eps"]["median"]
    h_tp = summary["hardened"]["throughput_eps"]["median"]
    tp_delta = ((h_tp - b_tp) / b_tp) * 100.0

    b_p50 = summary["baseline"]["proc_p50_us"]["median"]
    h_p50 = summary["hardened"]["proc_p50_us"]["median"]
    p50_delta = ((h_p50 - b_p50) / b_p50) * 100.0

    print("\n================================================================================")
    print("7-RUN REPRODUCIBILITY SUMMARY SCORECARD (MEDIANS & VARIABILITY)")
    print("================================================================================")
    print(f"Metric                       | Baseline (Pure Python)   | Hardened (Native C)      | Delta")
    print(f"-----------------------------+--------------------------+--------------------------+----------")
    print(f"Throughput Median (eps)      | {b_tp:12,.1f} (CV: {summary['baseline']['throughput_eps']['cv_pct']:4.1f}%) | {h_tp:12,.1f} (CV: {summary['hardened']['throughput_eps']['cv_pct']:4.1f}%) | {tp_delta:+6.1f}%")
    print(f"Processing Latency p50 (μs)  | {b_p50:12.1f} (CV: {summary['baseline']['proc_p50_us']['cv_pct']:4.1f}%) | {h_p50:12.1f} (CV: {summary['hardened']['proc_p50_us']['cv_pct']:4.1f}%) | {p50_delta:+6.1f}%")
    print(f"Processing Latency p95 (μs)  | {summary['baseline']['proc_p95_us']['median']:12.1f}             | {summary['hardened']['proc_p95_us']['median']:12.1f}             | {((summary['hardened']['proc_p95_us']['median'] - summary['baseline']['proc_p95_us']['median']) / summary['baseline']['proc_p95_us']['median']) * 100.0:+6.1f}%")
    print(f"Processing Latency p99 (μs)  | {summary['baseline']['proc_p99_us']['median']:12.1f}             | {summary['hardened']['proc_p99_us']['median']:12.1f}             | {((summary['hardened']['proc_p99_us']['median'] - summary['baseline']['proc_p99_us']['median']) / summary['baseline']['proc_p99_us']['median']) * 100.0:+6.1f}%")
    print(f"Quality Stage p50 (μs)       | {summary['baseline']['quality_p50_us']['median']:12.1f}             | {summary['hardened']['quality_p50_us']['median']:12.1f}             | {((summary['hardened']['quality_p50_us']['median'] - summary['baseline']['quality_p50_us']['median']) / summary['baseline']['quality_p50_us']['median']) * 100.0:+6.1f}%")
    print(f"Max RSS Memory (MB)          | {summary['baseline']['max_rss_mb']['median']:12.1f}             | {summary['hardened']['max_rss_mb']['median']:12.1f}             | {((summary['hardened']['max_rss_mb']['median'] - summary['baseline']['max_rss_mb']['median']) / summary['baseline']['max_rss_mb']['median']) * 100.0:+6.1f}%")
    print(f"================================================================================\n")

    return summary


if __name__ == "__main__":
    summary = run_alternating_benchmarks(num_runs=7, events=50000)
    os.makedirs("benchmarks", exist_ok=True)
    with open("benchmarks/alternating_benchmark_reproducibility.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("Benchmark results saved to benchmarks/alternating_benchmark_reproducibility.json")
