"""MDRAP Phase 4 Empirical Benchmark Suite (Workstream C).

Measures:
  1. Sustained Load Soak (50,000 events) with memory allocation tracking.
  2. Queue Saturation & Tail Latency (p50, p95, p99, p99.9).
  3. Per-stage pipeline micro-latency profile.
  4. Batch size throughput scalability and saturation knee curve.
Outputs to audit/phase4/benchmark_results.json.
"""

from __future__ import annotations

import gc
import json
import os
import struct
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.models import EventType, QualityStatus
from mdrap.quality import QualityEngine


def _compute_percentiles(latencies_ns: list[int]) -> dict[str, float]:
    if not latencies_ns:
        return {"p50_us": 0.0, "p95_us": 0.0, "p99_us": 0.0, "p999_us": 0.0, "max_us": 0.0}
    latencies_ns.sort()
    n = len(latencies_ns)
    return {
        "p50_us": round(latencies_ns[int(n * 0.50)] / 1000.0, 2),
        "p95_us": round(latencies_ns[int(n * 0.95)] / 1000.0, 2),
        "p99_us": round(latencies_ns[int(n * 0.99)] / 1000.0, 2),
        "p999_us": round(latencies_ns[min(int(n * 0.999), n - 1)] / 1000.0, 2),
        "max_us": round(latencies_ns[-1] / 1000.0, 2),
    }


def benchmark_sustained_soak(event_count: int = 50000) -> dict[str, Any]:
    print(f"[*] Running Sustained Load Soak Benchmark ({event_count:,} events)...")
    frames = [
        {"seq": i, "sym": "NVDA", "px": 120.0 + (i % 50) * 0.01, "sz": 25.0, "type": "TRADE"}
        for i in range(1, event_count + 1)
    ]
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="NASDAQ", feed_id="ITCH_DIRECT"),
        frames=frames,
    )
    adapter.connect()
    quality = QualityEngine()

    tracemalloc.start()
    gc.collect()
    mem_before_current, mem_before_peak = tracemalloc.get_traced_memory()

    latencies_ns: list[int] = []
    t0 = time.perf_counter()

    for _ in range(event_count):
        s0 = time.perf_counter_ns()
        raw = adapter.poll()
        if raw is not None:
            canon = adapter.normalize(raw)
            _ = quality.evaluate(canon)
        s1 = time.perf_counter_ns()
        latencies_ns.append(s1 - s0)

    total_time_s = time.perf_counter() - t0
    mem_after_current, mem_after_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rate = round(event_count / total_time_s, 1)
    pct = _compute_percentiles(latencies_ns)

    return {
        "event_count": event_count,
        "elapsed_s": round(total_time_s, 4),
        "throughput_eps": rate,
        "mem_delta_mb": round((mem_after_current - mem_before_current) / (1024 * 1024), 3),
        "mem_peak_mb": round(mem_after_peak / (1024 * 1024), 3),
        **pct,
    }


def benchmark_pipeline_stage_profile(event_count: int = 20000) -> dict[str, Any]:
    print(f"[*] Running Stage Breakdown Micro-Latency Profile ({event_count:,} events)...")
    frames = [
        {"seq": i, "sym": "MSFT", "px": 420.0 + (i % 20) * 0.05, "sz": 50.0, "type": "TRADE"}
        for i in range(1, event_count + 1)
    ]
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="NASDAQ", feed_id="ITCH_DIRECT"),
        frames=frames,
    )
    adapter.connect()
    quality = QualityEngine()

    poll_ns: list[int] = []
    norm_ns: list[int] = []
    eval_ns: list[int] = []
    sbe_ns: list[int] = []

    for _ in range(event_count):
        t0 = time.perf_counter_ns()
        raw = adapter.poll()
        t1 = time.perf_counter_ns()
        canon = adapter.normalize(raw)  # type: ignore
        t2 = time.perf_counter_ns()
        ev_res = quality.evaluate(canon)
        t3 = time.perf_counter_ns()

        # SBE pack
        inst_b = canon.instrument_id.encode("ascii").ljust(16, b"\x00")
        _ = struct.pack(
            "<16sQqddII8s",
            inst_b,
            canon.sequence_number or 0,
            int(canon.exchange_timestamp * 1e9),
            canon.price or 0.0,
            canon.quantity or 0.0,
            1,
            0,
            b"\x00" * 8,
        )
        t4 = time.perf_counter_ns()

        poll_ns.append(t1 - t0)
        norm_ns.append(t2 - t1)
        eval_ns.append(t3 - t2)
        sbe_ns.append(t4 - t3)

    return {
        "event_count": event_count,
        "poll_stage": _compute_percentiles(poll_ns),
        "normalize_stage": _compute_percentiles(norm_ns),
        "quality_eval_stage": _compute_percentiles(eval_ns),
        "sbe_pack_stage": _compute_percentiles(sbe_ns),
    }


def benchmark_capacity_scaling() -> list[dict[str, Any]]:
    print("[*] Running Capacity Saturation Sweep across batch sizes...")
    batch_sizes = [1000, 5000, 20000, 50000]
    sweep_results = []

    for count in batch_sizes:
        frames = [
            {"seq": i, "sym": "SPY", "px": 550.0, "sz": 100.0, "type": "TRADE"}
            for i in range(1, count + 1)
        ]
        adapter = ReplayFeedAdapter(
            FeedAdapterConfig(venue="ARCA", feed_id="DIRECT"),
            frames=frames,
        )
        adapter.connect()
        quality = QualityEngine()

        t0 = time.perf_counter()
        for _ in range(count):
            r = adapter.poll()
            if r is not None:
                c = adapter.normalize(r)
                _ = quality.evaluate(c)
        elapsed = time.perf_counter() - t0
        rate = round(count / elapsed, 1)

        sweep_results.append({
            "batch_size": count,
            "elapsed_s": round(elapsed, 4),
            "rate_eps": rate,
        })
        adapter.disconnect()

    return sweep_results


def run_phase4_benchmarks() -> dict[str, Any]:
    print("=== MDRAP Phase 4 Production Benchmark Suite ===")
    results = {
        "timestamp": time.time(),
        "sustained_soak": benchmark_sustained_soak(50000),
        "stage_profile": benchmark_pipeline_stage_profile(20000),
        "capacity_scaling": benchmark_capacity_scaling(),
    }

    out_path = os.path.join("audit", "phase4", "benchmark_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n[+] Phase 4 Benchmark completed successfully. Output written to {out_path}")
    return results


if __name__ == "__main__":
    run_phase4_benchmarks()
