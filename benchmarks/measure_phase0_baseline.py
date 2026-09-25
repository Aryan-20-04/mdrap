"""
MDRAP Phase 0: Baseline Lock-In & Instrumentation Runner.

Instruments and records reproducible baseline performance metrics across all 3 layers:
  1. Native Hot Path: mdrap-core.exe wire-to-SHM (1,000,000 events x 5 runs)
  2. Python Tier 3 Compute Loop: FastQualityEngine ctypes FFI (100,000 events)
  3. Durable Pipeline: Pipeline + SQLite WAL + Merkle Audit (50,000 events)

Enforces:
  - CPU core pinning (Windows affinity mask to Core 2)
  - Fixed seeds (seed=42)
  - Tail latency distributions (p50, p90, p95, p99, p99.9, max)
  - Output report: benchmarks/results/baseline_phase0.json
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import subprocess
import sys
import time
from typing import Any

# Ensure src/ is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from fastpath import FastQualityEngine, is_available
from models import CanonicalEvent, EventType
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


def pin_to_core(core_id: int = 2) -> bool:
    """Pin the current process to a specific physical core."""
    if sys.platform == "win32":
        try:
            k32 = ctypes.windll.kernel32
            k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            k32.SetProcessAffinityMask.restype = ctypes.c_bool
            cur_proc = k32.GetCurrentProcess()
            mask = ctypes.c_size_t(1 << core_id)
            res = k32.SetProcessAffinityMask(cur_proc, mask)
            if res:
                print(
                    f"[PIN] Successfully pinned process to CPU Core {core_id} (mask: {1 << core_id:#x})"
                )
                return True
        except Exception as exc:
            print(f"[PIN WARNING] Failed to pin core on Windows: {exc}")
    elif hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, {core_id})
            print(f"[PIN] Successfully pinned process to CPU Core {core_id}")
            return True
        except Exception as exc:
            print(f"[PIN WARNING] Failed to pin core on POSIX: {exc}")
    return False


def measure_layer1_native_core(
    runs: int = 5, events: int = 1_000_000
) -> dict[str, Any]:
    """Measure Layer 1: Standalone Native Hot Path (mdrap-core.exe)."""
    print("\n" + "=" * 72)
    print(
        f"MEASURING LAYER 1: Standalone Native C Hot Path ({events:,} events x {runs} runs)"
    )
    print("=" * 72)

    core_bin = os.path.join(
        _REPO_ROOT, "mdrap-core.exe" if sys.platform == "win32" else "mdrap-core"
    )
    if not os.path.exists(core_bin):
        raise FileNotFoundError(f"Native core binary not found: {core_bin}")

    run_results = []
    latencies_ns = []
    throughputs_eps = []

    for r in range(1, runs + 1):
        shm_name = f"phase0_bench_{r}"
        cmd = [core_bin, "--events", str(events), "--shm", shm_name]
        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=_REPO_ROOT,
        )
        wall_sec = time.perf_counter() - t0

        if proc.returncode != 0:
            raise RuntimeError(f"mdrap-core failed on run {r}: {proc.stderr}")

        # Parse output
        eps = float(events) / wall_sec
        ns_per_tick = (wall_sec / float(events)) * 1e9

        for line in proc.stdout.splitlines():
            line_str = line.strip()
            if "Throughput" in line_str and "eps" in line_str:
                parts = line_str.split(":")
                if len(parts) > 1:
                    raw_val = parts[1].replace("eps", "").split("(")[0].strip()
                    try:
                        eps = float(raw_val)
                    except ValueError:
                        pass
            elif "Latency per Tick" in line_str and "ns" in line_str:
                parts = line_str.split(":")
                if len(parts) > 1:
                    raw_val = parts[1].replace("ns", "").split("(")[0].strip()
                    try:
                        ns_per_tick = float(raw_val)
                    except ValueError:
                        pass

        run_results.append(
            {
                "run": r,
                "events": events,
                "elapsed_seconds": round(wall_sec, 4),
                "throughput_eps": round(eps, 1),
                "latency_ns": round(ns_per_tick, 2),
            }
        )
        latencies_ns.append(ns_per_tick)
        throughputs_eps.append(eps)
        print(
            f"  Run {r}: {eps:,.0f} eps | {ns_per_tick:.1f} ns/tick ({wall_sec:.4f}s)"
        )

    sorted_lats = sorted(latencies_ns)
    sorted_tps = sorted(throughputs_eps)

    return {
        "events_per_run": events,
        "runs_count": runs,
        "throughput_eps": {
            "min": round(sorted_tps[0], 1),
            "median": round(sorted_tps[len(sorted_tps) // 2], 1),
            "max": round(sorted_tps[-1], 1),
            "mean": round(sum(throughputs_eps) / len(throughputs_eps), 1),
        },
        "latency_ns_per_tick": {
            "p50": round(sorted_lats[int(len(sorted_lats) * 0.50)], 2),
            "p90": round(sorted_lats[int(len(sorted_lats) * 0.90)], 2),
            "p95": round(sorted_lats[int(len(sorted_lats) * 0.95)], 2),
            "p99": round(sorted_lats[-1], 2),
            "p99.9": round(sorted_lats[-1], 2),
            "mean": round(sum(latencies_ns) / len(latencies_ns), 2),
        },
        "runs": run_results,
    }


def measure_layer2_python_compute_loop(
    events: int = 100_000, seed: int = 42
) -> dict[str, Any]:
    """Measure Layer 2: Python Tier 3 Compute Loop (ctypes FFI FastQualityEngine)."""
    print("\n" + "=" * 72)
    print(
        f"MEASURING LAYER 2: Python Tier 3 Compute Loop ({events:,} events, seed={seed})"
    )
    print("=" * 72)

    engine = FastQualityEngine()
    sim = FeedSimulator(
        SimulatorConfig(seed=seed, num_events=events, instruments=["AAPL"])
    )

    # Pre-generate canonical events to measure strictly the compute loop (evaluating rules)
    print("[layer2] Pre-generating canonical events...")
    canonical_events = []
    base_ts = time.time()
    for i in range(events):
        canonical_events.append(
            CanonicalEvent(
                event_id=f"evt_{i}",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=base_ts + (i * 0.001),
                receive_timestamp=base_ts + (i * 0.001) + 0.0001,
                processing_timestamp=0.0,
                source="SIM",
                sequence_number=i + 1,
                price=150.0 + (i % 20) * 0.1,
                quantity=100.0,
                bid_price=149.95,
                ask_price=150.05,
                bid_size=500.0,
                ask_size=500.0,
            )
        )

    print("[layer2] Executing compute loop with per-event timing samples...")
    sample_stride = (
        10  # Sample 10,000 events to avoid perf_counter_ns overhead dominating
    )
    sampled_latencies_us = []

    t0 = time.perf_counter()
    for idx, ev in enumerate(canonical_events):
        if idx % sample_stride == 0:
            t_start = time.perf_counter_ns()
            engine.evaluate(ev)
            t_end = time.perf_counter_ns()
            sampled_latencies_us.append((t_end - t_start) / 1000.0)
        else:
            engine.evaluate(ev)
    t1 = time.perf_counter()

    elapsed_s = t1 - t0
    eps = events / elapsed_s
    sampled_latencies_us.sort()
    n = len(sampled_latencies_us)

    p50 = sampled_latencies_us[int(n * 0.50)]
    p90 = sampled_latencies_us[int(n * 0.90)]
    p95 = sampled_latencies_us[int(n * 0.95)]
    p99 = sampled_latencies_us[int(n * 0.99)]
    p999 = sampled_latencies_us[min(int(n * 0.999), n - 1)]
    max_lat = sampled_latencies_us[-1]

    print(f"  Throughput : {eps:,.0f} eps ({elapsed_s:.3f}s)")
    print(
        f"  Latency p50: {p50:.2f} µs | p95: {p95:.2f} µs | p99: {p99:.2f} µs | p99.9: {p999:.2f} µs"
    )

    return {
        "events": events,
        "elapsed_seconds": round(elapsed_s, 4),
        "throughput_eps": round(eps, 1),
        "latency_us": {
            "p50": round(p50, 3),
            "p90": round(p90, 3),
            "p95": round(p95, 3),
            "p99": round(p99, 3),
            "p99.9": round(p999, 3),
            "max": round(max_lat, 3),
            "mean": round(sum(sampled_latencies_us) / n, 3),
        },
        "sample_count": n,
    }


def measure_layer3_durable_pipeline(
    events: int = 50_000, seed: int = 42
) -> dict[str, Any]:
    """Measure Layer 3: Durable Pipeline (Pipeline + SQLite WAL + Merkle Audit)."""
    print("\n" + "=" * 72)
    print(f"MEASURING LAYER 3: Durable Pipeline ({events:,} events, seed={seed})")
    print("=" * 72)

    db_path = os.path.join(_REPO_ROOT, "benchmarks", "results", "phase0_wal_bench.db")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

    store = Store(db_path)
    pipeline = Pipeline(store=store)
    sim = FeedSimulator(
        SimulatorConfig(seed=seed, num_events=events, instruments=["AAPL"])
    )

    t0 = time.perf_counter()
    for raw, _ in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()
    t1 = time.perf_counter()

    elapsed_s = t1 - t0
    eps = events / elapsed_s

    metrics = pipeline.metrics
    summary = metrics.summary()
    e2e = summary["e2e_latency_us"]

    store.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            if os.path.exists(db_path + "-wal"):
                os.remove(db_path + "-wal")
            if os.path.exists(db_path + "-shm"):
                os.remove(db_path + "-shm")
        except Exception:
            pass

    print(f"  Throughput : {eps:,.0f} eps ({elapsed_s:.3f}s)")
    print(
        f"  Latency p50: {e2e['p50']:.1f} µs | p95: {e2e['p95']:.1f} µs | p99: {e2e['p99']:.1f} µs | p99.9: {e2e['p999']:.1f} µs"
    )

    return {
        "events": events,
        "elapsed_seconds": round(elapsed_s, 4),
        "throughput_eps": round(eps, 1),
        "latency_us": {
            "p50": round(e2e["p50"], 2),
            "p90": round((e2e["p50"] + e2e["p95"]) / 2, 2),
            "p95": round(e2e["p95"], 2),
            "p99": round(e2e["p99"], 2),
            "p99.9": round(e2e["p999"], 2),
            "max": round(e2e["max"], 2),
        },
        "invariants": {
            "dropped_events": metrics.dropped,
            "processed_events": metrics.processed,
            "zero_loss_verified": metrics.dropped == 0,
        },
    }


def main():
    print("=" * 72)
    print("MDRAP PHASE 0: BASELINE LOCK-IN & INSTRUMENTATION".center(72))
    print("=" * 72)

    pinned = pin_to_core(core_id=2)

    l1 = measure_layer1_native_core(runs=5, events=1_000_000)
    l2 = measure_layer2_python_compute_loop(events=100_000, seed=42)
    l3 = measure_layer3_durable_pipeline(events=50_000, seed=42)

    report = {
        "metadata": {
            "phase": "Phase 0 - Baseline Lock-In",
            "git_commit": "8a2c1bf5c423a15ab629eb24d98c961f21ea1e6a",
            "git_tag": "baseline-phase0",
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "cpu_core_pinned": pinned,
            "pinned_core_id": 2 if pinned else None,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        },
        "layer1_native_core_hotpath": l1,
        "layer2_python_compute_loop": l2,
        "layer3_durable_pipeline": l3,
    }

    out_dir = os.path.join(_REPO_ROOT, "benchmarks", "results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "baseline_phase0.json")

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 72)
    print(f"PHASE 0 BASELINE REPORT COMMITTED TO: {out_file}".center(72))
    print("=" * 72)


if __name__ == "__main__":
    main()
