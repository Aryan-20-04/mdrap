"""
MDRAP Multi-Source Contention Benchmark (Phase 19).
Demonstrates flat p99.9 tail latency of zero-lock single-writer ingestion vs.
mutex-locked multi-source ingestion under thread contention (1, 2, 4, 8 sources).
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from term import Console, Table, Panel


def percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[int(f)] * (c - k) + s[int(c)] * (k - f))


def run_locked_contention(num_sources: int, events_per_source: int = 5000) -> tuple[float, list[float]]:
    """Simulate multi-source ingestion contending on a single mutex lock."""
    lock = threading.Lock()
    all_latencies_us: list[float] = []
    lat_lock = threading.Lock()
    state = {"seq": 0, "last_price": 80000.0}

    barrier = threading.Barrier(num_sources)

    def worker(src_id: int):
        latencies: list[float] = []
        barrier.wait()
        for i in range(events_per_source):
            t0 = time.perf_counter_ns()
            with lock:
                # Critical section: simulate event ingestion & sequence assignment
                state["seq"] += 1
                state["last_price"] += 0.01 * (src_id + 1)
                _ = state["seq"]
            t1 = time.perf_counter_ns()
            if i % 5 == 0:  # Sample every 5th event
                latencies.append((t1 - t0) / 1000.0)

        with lat_lock:
            all_latencies_us.extend(latencies)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_sources)]
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_total = time.perf_counter() - t_start

    total_events = num_sources * events_per_source
    eps = total_events / t_total if t_total > 0 else 0
    return eps, all_latencies_us


def run_lockfree_contention(num_sources: int, events_per_source: int = 5000) -> tuple[float, list[float]]:
    """Simulate multi-source ingestion with partitioned lock-free execution (zero locks)."""
    all_latencies_us: list[float] = []
    lat_lock = threading.Lock()

    barrier = threading.Barrier(num_sources)

    def worker(src_id: int):
        # Dedicated per-source partition / state (lock-free single writer)
        src_seq = 0
        src_price = 80000.0
        latencies: list[float] = []
        barrier.wait()
        for i in range(events_per_source):
            t0 = time.perf_counter_ns()
            # Lock-free update: thread-local partition
            src_seq += 1
            src_price += 0.01 * (src_id + 1)
            _ = src_seq
            t1 = time.perf_counter_ns()
            if i % 5 == 0:  # Sample every 5th event
                latencies.append((t1 - t0) / 1000.0)

        with lat_lock:
            all_latencies_us.extend(latencies)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_sources)]
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_total = time.perf_counter() - t_start

    total_events = num_sources * events_per_source
    eps = total_events / t_total if t_total > 0 else 0
    return eps, all_latencies_us


def main():
    console = Console()
    console.print(Panel("[bold cyan]MDRAP Lock-Free vs Locked Contention Benchmark[/bold cyan]\n[dim]Evaluating tail latency (p50, p95, p99, p99.9) under multi-source contention[/dim]", border_style="cyan"))

    concurrency_levels = [1, 2, 4, 8]
    events_per_source = 10000
    results: dict[str, Any] = {
        "timestamp": time.time(),
        "events_per_source": events_per_source,
        "concurrency_sweep": [],
    }

    t = Table(title="Multi-Source Contention & Tail Latency (p99.9)", show_lines=True)
    t.add_column("Sources", style="bold white", justify="center")
    t.add_column("Architecture", style="cyan")
    t.add_column("Throughput (eps)", justify="right")
    t.add_column("p50 (µs)", justify="right")
    t.add_column("p95 (µs)", justify="right")
    t.add_column("p99 (µs)", justify="right")
    t.add_column("p99.9 (µs)", justify="right", style="bold yellow")
    t.add_column("Tail Ratio (p99.9/p50)", justify="right")

    for sources in concurrency_levels:
        # Run locked
        eps_locked, lat_locked = run_locked_contention(sources, events_per_source)
        p50_l = percentile(lat_locked, 50.0)
        p95_l = percentile(lat_locked, 95.0)
        p99_l = percentile(lat_locked, 99.0)
        p99_9_l = percentile(lat_locked, 99.9)
        ratio_l = p99_9_l / p50_l if p50_l > 0 else 1.0

        # Run lock-free
        eps_lf, lat_lf = run_lockfree_contention(sources, events_per_source)
        p50_lf = percentile(lat_lf, 50.0)
        p95_lf = percentile(lat_lf, 95.0)
        p99_lf = percentile(lat_lf, 99.0)
        p99_9_lf = percentile(lat_lf, 99.9)
        ratio_lf = p99_9_lf / p50_lf if p50_lf > 0 else 1.0

        t.add_row(
            str(sources),
            "[red]Locked Mutex[/red]",
            f"{eps_locked:,.0f}",
            f"{p50_l:.2f}",
            f"{p95_l:.2f}",
            f"{p99_l:.2f}",
            f"{p99_9_l:.2f}",
            f"{ratio_l:.1f}x",
        )
        t.add_row(
            str(sources),
            "[green]Lock-Free SPSC (T1)[/green]",
            f"{eps_lf:,.0f}",
            f"{p50_lf:.2f}",
            f"{p95_lf:.2f}",
            f"{p99_lf:.2f}",
            f"[bold green]{p99_9_lf:.2f}[/bold green]",
            f"{ratio_lf:.1f}x",
        )

        results["concurrency_sweep"].append({
            "sources": sources,
            "locked": {
                "throughput_eps": eps_locked,
                "p50_us": p50_l,
                "p95_us": p95_l,
                "p99_us": p99_l,
                "p99_9_us": p99_9_l,
                "tail_ratio": ratio_l,
            },
            "lockfree": {
                "throughput_eps": eps_lf,
                "p50_us": p50_lf,
                "p95_us": p95_lf,
                "p99_us": p99_lf,
                "p99_9_us": p99_9_lf,
                "tail_ratio": ratio_lf,
            },
        })

    console.print(t)

    out_file = os.path.join(os.path.dirname(__file__), "contention_results.json")
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    console.print(f"[dim]Saved contention benchmark results to {out_file}[/dim]\n")


if __name__ == "__main__":
    main()
