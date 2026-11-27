"""
MDRAP 6.0.8 — Rigorous Alternating Release Benchmark Verification Suite.

Corrects the baseline comparison methodology by strictly evaluating:
1. Pair 1 (Native C): Baseline Native C vs Hardened Native C (7 alternating runs)
2. Pair 2 (Pure Python): Baseline Pure Python vs Hardened Pure Python (7 alternating runs)

Attributes language/accelerator deltas separately from architectural hardening deltas.
Measures: EPS, p50, p95, p99, p99.9, max, wall time, stage breakdowns, and CV%.
"""

from __future__ import annotations

import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from typing import Any

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASELINE_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "mdrap_baseline"))


def run_benchmark_command(
    cwd: str,
    fastpath: bool,
    seed: int = 42,
    events: int = 50000,
    label: str = "run",
) -> dict[str, Any]:
    """Execute cli.py benchmark in the given workspace directory."""
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
    if fastpath:
        cmd.append("--fastpath")
    else:
        cmd.append("--no-fastpath")

    t0 = time.perf_counter()
    env = os.environ.copy()
    env["MDRAP_API_KEY_SALT"] = env.get(
        "MDRAP_API_KEY_SALT", "benchmark_salt_secret_123"
    )
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    wall_duration = time.perf_counter() - t0

    stdout = res.stdout.strip()
    json_start = stdout.find("{")
    if json_start < 0:
        raise ValueError(f"No JSON in benchmark output from {cwd}: {stdout}")
    data = json.loads(stdout[json_start:])

    perf = data.get("performance", {})
    proc_lat = perf.get("processing_latency_us", {})
    e2e_lat = perf.get("e2e_latency_us", {})
    stages = perf.get("stages_us", {})

    return {
        "label": label,
        "accelerator": data.get("accelerator", "unknown"),
        "code_version": data.get("code_version", "unknown"),
        "wall_duration_s": round(wall_duration, 4),
        "benchmark_elapsed_s": round(perf.get("elapsed_s", 0.0), 4),
        "throughput_eps": round(perf.get("throughput_eps", 0.0), 1),
        "proc_p50_us": proc_lat.get("p50", 0.0),
        "proc_p95_us": proc_lat.get("p95", 0.0),
        "proc_p99_us": proc_lat.get("p99", 0.0),
        "proc_p999_us": proc_lat.get("p999", proc_lat.get("p99", 0.0)),
        "proc_max_us": proc_lat.get("max", 0.0),
        "e2e_p50_us": e2e_lat.get("p50", 0.0),
        "e2e_p95_us": e2e_lat.get("p95", 0.0),
        "e2e_p99_us": e2e_lat.get("p99", 0.0),
        "e2e_p999_us": e2e_lat.get("p999", 0.0),
        "e2e_max_us": e2e_lat.get("max", 0.0),
        "ingest_p50_us": stages.get("ingest_normalize", {}).get("p50", 0.0),
        "quality_p50_us": stages.get("quality_evaluate", {}).get("p50", 0.0),
        "reconcile_p50_us": stages.get("reconcile_analytics", {}).get("p50", 0.0),
        "enqueue_p50_us": stages.get("enqueue_storage", {}).get("p50", 0.0),
        "max_rss_mb": perf.get("max_rss_mb", 0.0),
    }


def compute_series_stats(series: list[float]) -> dict[str, float]:
    """Compute median, mean, stdev, CV%, min, max."""
    if not series:
        return {}
    med = statistics.median(series)
    mean = statistics.mean(series)
    stdev = statistics.stdev(series) if len(series) > 1 else 0.0
    cv_pct = (stdev / mean * 100.0) if mean > 0 else 0.0
    return {
        "median": round(med, 2),
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
        "cv_pct": round(cv_pct, 2),
        "min": round(min(series), 2),
        "max": round(max(series), 2),
    }


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics across multiple runs."""
    keys = [
        "throughput_eps",
        "benchmark_elapsed_s",
        "proc_p50_us",
        "proc_p95_us",
        "proc_p99_us",
        "proc_p999_us",
        "proc_max_us",
        "e2e_p50_us",
        "e2e_p95_us",
        "e2e_p99_us",
        "e2e_p999_us",
        "ingest_p50_us",
        "quality_p50_us",
        "reconcile_p50_us",
        "enqueue_storage_p50_us"
        if "enqueue_storage_p50_us" in runs[0]
        else "enqueue_p50_us",
        "max_rss_mb",
    ]
    summary = {}
    for k in keys:
        vals = [r[k] for r in runs if k in r]
        if vals:
            summary[k] = compute_series_stats(vals)
    return summary


def run_alternating_pair(
    pair_name: str,
    fastpath: bool,
    n_runs: int = 7,
    events: int = 50000,
    seed: int = 42,
) -> dict[str, Any]:
    """Run n_runs alternating between baseline and hardened."""
    print(f"\n=======================================================")
    print(f"Executing Alternating Benchmark: {pair_name}")
    print(f"Workload: {events:,} events, seed {seed}, {n_runs} runs per target")
    print(f"=======================================================")

    baseline_runs = []
    hardened_runs = []

    for i in range(1, n_runs + 1):
        # 1. Baseline
        b_label = f"baseline_{'c' if fastpath else 'py'}_run_{i}"
        print(
            f"[{pair_name}] Iteration {i}/{n_runs} -> Baseline...", end="", flush=True
        )
        b_res = run_benchmark_command(
            cwd=BASELINE_DIR,
            fastpath=fastpath,
            seed=seed,
            events=events,
            label=b_label,
        )
        baseline_runs.append(b_res)
        print(
            f" Done ({b_res['throughput_eps']:,.1f} eps, p50: {b_res['proc_p50_us']} μs)"
        )

        # 2. Hardened
        h_label = f"hardened_{'c' if fastpath else 'py'}_run_{i}"
        print(
            f"[{pair_name}] Iteration {i}/{n_runs} -> Hardened...", end="", flush=True
        )
        h_res = run_benchmark_command(
            cwd=ROOT_DIR,
            fastpath=fastpath,
            seed=seed,
            events=events,
            label=h_label,
        )
        hardened_runs.append(h_res)
        print(
            f" Done ({h_res['throughput_eps']:,.1f} eps, p50: {h_res['proc_p50_us']} μs)"
        )

    b_summary = aggregate_runs(baseline_runs)
    h_summary = aggregate_runs(hardened_runs)

    # Calculate Deltas
    deltas = {}
    for metric in [
        "throughput_eps",
        "proc_p50_us",
        "proc_p95_us",
        "proc_p99_us",
        "benchmark_elapsed_s",
    ]:
        b_med = b_summary[metric]["median"]
        h_med = h_summary[metric]["median"]
        pct_change = ((h_med - b_med) / b_med * 100.0) if b_med > 0 else 0.0
        deltas[metric] = {
            "baseline_median": b_med,
            "hardened_median": h_med,
            "delta_pct": round(pct_change, 2),
        }

    return {
        "pair_name": pair_name,
        "fastpath": fastpath,
        "iterations": n_runs,
        "workload_events": events,
        "seed": seed,
        "baseline_summary": b_summary,
        "hardened_summary": h_summary,
        "deltas": deltas,
        "raw_baseline_runs": baseline_runs,
        "raw_hardened_runs": hardened_runs,
    }


def main():
    if not os.path.exists(BASELINE_DIR):
        print(f"Error: Baseline directory {BASELINE_DIR} not found.")
        sys.exit(1)

    print("Platform Architecture:")
    print(f"  OS: {platform.platform()}")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  CPU Cores: {os.cpu_count()}")

    # 1. Pair 1: Native C (Baseline vs Hardened)
    c_results = run_alternating_pair(
        pair_name="Native C (Baseline vs Hardened)",
        fastpath=True,
        n_runs=7,
        events=50000,
        seed=42,
    )

    # 2. Pair 2: Pure Python (Baseline vs Hardened)
    py_results = run_alternating_pair(
        pair_name="Pure Python (Baseline vs Hardened)",
        fastpath=False,
        n_runs=7,
        events=50000,
        seed=42,
    )

    # Combined Output
    output = {
        "metadata": {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "cpu_cores": os.cpu_count(),
            "timestamp": time.time(),
            "methodology": "Strict Intra-Runtime Alternating Benchmark (7 runs per target)",
            "workload": "50,000 events, seed 42",
        },
        "native_c_pair": c_results,
        "pure_python_pair": py_results,
    }

    out_path = os.path.join(
        ROOT_DIR, "benchmarks", "release_benchmarks_reproducibility.json"
    )
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n=======================================================")
    print(f"Benchmark Verification Successfully Completed!")
    print(f"Saved: {out_path}")
    print(f"=======================================================")

    print("\n--- Summary: Native C Pair ---")
    for k, v in c_results["deltas"].items():
        print(
            f"  {k}: Baseline {v['baseline_median']} -> Hardened {v['hardened_median']} ({v['delta_pct']:+.2f}%)"
        )

    print("\n--- Summary: Pure Python Pair ---")
    for k, v in py_results["deltas"].items():
        print(
            f"  {k}: Baseline {v['baseline_median']} -> Hardened {v['hardened_median']} ({v['delta_pct']:+.2f}%)"
        )


if __name__ == "__main__":
    main()
