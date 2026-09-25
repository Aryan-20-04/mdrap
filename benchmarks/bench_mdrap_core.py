"""
MDRAP Standalone Native Core Benchmark (mdrap-core).
Measures end-to-end wire-to-SHM single-tick execution latency and throughput
of the standalone C hot-path process across multiple runs.
Generates committed JSON benchmark report: benchmarks/mdrap_core_bench.json.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import build_fastpath


def run_benchmark(
    events: int = 1_000_000, runs: int = 5, output_file: str | None = None
) -> dict:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    core_bin_name = build_fastpath.get_core_bin_name()
    core_bin_path = os.path.join(repo_root, core_bin_name)

    if not os.path.exists(core_bin_path):
        print(f"[bench] Building {core_bin_name}...")
        built = build_fastpath.build_core(target_dir=repo_root, quiet=False)
        if not built or not os.path.exists(core_bin_path):
            raise RuntimeError(f"Failed to build {core_bin_name}")

    results = []
    print(
        f"[bench] Executing {runs} runs of {events:,} events each with {core_bin_name}..."
    )

    # Pattern match output
    # Events Processed : 1000000
    # Elapsed Time     : 0.0568 seconds
    # Throughput       : 17592873 eps (17.59M eps)
    # Latency per Tick : 56.8 ns (0.057 µs)
    pat_eps = re.compile(r"Throughput\s*:\s*([0-9]+)\s*eps")
    pat_ns = re.compile(r"Latency per Tick\s*:\s*([0-9.]+)\s*ns")
    pat_time = re.compile(r"Elapsed Time\s*:\s*([0-9.]+)\s*seconds")

    for r in range(1, runs + 1):
        cmd = [core_bin_path, "--events", str(events), "--shm", f"bench_core_{r}"]
        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=repo_root,
        )
        wall_time = time.perf_counter() - t0

        if proc.returncode != 0:
            raise RuntimeError(f"Run {r} failed: {proc.stderr}")

        m_eps = pat_eps.search(proc.stdout)
        m_ns = pat_ns.search(proc.stdout)
        m_time = pat_time.search(proc.stdout)

        eps = float(m_eps.group(1)) if m_eps else (events / wall_time)
        ns = float(m_ns.group(1)) if m_ns else (wall_time / events * 1e9)
        el_sec = float(m_time.group(1)) if m_time else wall_time

        results.append(
            {
                "run": r,
                "events": events,
                "elapsed_seconds": el_sec,
                "throughput_eps": eps,
                "latency_ns_per_tick": ns,
            }
        )
        print(f"  Run {r}: {eps:,.0f} eps | {ns:.1f} ns/tick ({el_sec:.4f}s)")

    throughputs = [res["throughput_eps"] for res in results]
    latencies = [res["latency_ns_per_tick"] for res in results]

    sorted_tp = sorted(throughputs)
    sorted_lat = sorted(latencies)
    mid = len(sorted_tp) // 2

    summary = {
        "metadata": {
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "binary": core_bin_name,
            "runs": runs,
            "events_per_run": events,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        },
        "throughput_eps": {
            "min": sorted_tp[0],
            "median": sorted_tp[mid],
            "max": sorted_tp[-1],
            "mean": sum(throughputs) / len(throughputs),
        },
        "latency_ns_per_tick": {
            "min": sorted_lat[0],
            "median": sorted_lat[mid],
            "max": sorted_lat[-1],
            "mean": sum(latencies) / len(latencies),
        },
        "runs_detail": results,
    }

    if output_file:
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"\n[bench] Wrote benchmark report to {output_file}")

    return summary


if __name__ == "__main__":
    out_path = os.path.join(os.path.dirname(__file__), "mdrap_core_bench.json")
    run_benchmark(events=1_000_000, runs=5, output_file=out_path)
