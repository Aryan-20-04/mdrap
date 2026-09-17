"""
Hot-Path Cost Breakdown Profiler for MDRAP V4 Pipeline.

Runs the V4 native C pipeline on 100,000 deterministic events (seed=42)
under cProfile and high-resolution timing, attributing microseconds per event to:
  1. gateway.normalize (RawEvent -> CanonicalEvent construction)
  2. ctypes call boundary tax
  3. C-side quality evaluation logic
  4. reconciliation (reconciler + reliability tracker)
  5. storage batch flush (SQLite write + lineage/quarantine enqueue)

Acceptance: Stage costs sum to within 10% of measured end-to-end processing p50.
Emits baseline_<date>.json and evaluates the Phase 0 Gate.
"""
from __future__ import annotations

import argparse
import cProfile
import datetime
import io
import json
import os
import pstats
import sys
import time
from typing import Any

# Ensure src/ and benchmarks/ are on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from fastpath import FastQualityEngine, _NATIVE_LIB, is_available
from micro_ffi import run_micro_ffi
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


def profile_pipeline(events: int = 100_000, seed: int = 42, ffi_samples: int = 1_000_000) -> dict[str, Any]:
    print(f"[*] Step 1/3: Calibrating ctypes FFI boundary tax ({ffi_samples:,} iterations)...")
    ffi_res = run_micro_ffi(iterations=ffi_samples, runs=3, warmup=1)
    ffi_boundary_tax_us = ffi_res["ffi_boundary_tax_ns"] / 1000.0
    c_logic_us = ffi_res["c_logic_ns"] / 1000.0

    print(f"    -> ctypes boundary tax: {ffi_res['ffi_boundary_tax_ns']:.2f} ns ({ffi_boundary_tax_us:.3f} µs)")
    print(f"    -> C-side quality logic: {ffi_res['c_logic_ns']:.2f} ns ({c_logic_us:.3f} µs)")

    print(f"\n[*] Step 2/3: Initializing V4 pipeline and generator ({events:,} events, seed={seed})...")
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=events))
    store = Store(":memory:")
    quality = FastQualityEngine()
    pipeline = Pipeline(store, quality=quality)

    print(f"\n[*] Step 3/3: Running V4 pipeline under cProfile and metrics instrumentation...")
    pr = cProfile.Profile()
    pr.enable()

    t_start = time.perf_counter_ns()
    for raw, _label in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()
    t_end = time.perf_counter_ns()

    pr.disable()
    store.close()

    total_pipeline_time_s = (t_end - t_start) / 1_000_000_000.0
    throughput_eps = events / total_pipeline_time_s if total_pipeline_time_s > 0 else 0.0

    # Extract metrics summary from pipeline
    summary = pipeline.metrics.summary()
    proc_lat = summary["processing_latency_us"]
    p50_total = proc_lat["p50"]
    p95_total = proc_lat["p95"]
    p99_total = proc_lat["p99"]

    stages = summary.get("stages_us", {})
    p50_norm = stages.get("ingest_normalize", {}).get("p50", 0.0)
    p50_qual = stages.get("quality_evaluate", {}).get("p50", 0.0)
    p50_rec = stages.get("reconcile_analytics", {}).get("p50", 0.0)
    p50_store = stages.get("enqueue_storage", {}).get("p50", 0.0)

    # Decompose quality stage into FFI boundary, C logic, and Python wrapper
    ffi_bound_actual = min(ffi_boundary_tax_us, p50_qual)
    c_logic_actual = min(c_logic_us, max(0.0, p50_qual - ffi_bound_actual))
    py_wrapper_actual = max(0.0, p50_qual - ffi_bound_actual - c_logic_actual)

    stage_breakdown = {
        "1_gateway_normalize_us": round(p50_norm, 3),
        "2_ctypes_ffi_boundary_us": round(ffi_bound_actual, 3),
        "3_c_side_quality_eval_us": round(c_logic_actual, 3),
        "4_py_quality_wrapper_us": round(py_wrapper_actual, 3),
        "5_reconciliation_us": round(p50_rec, 3),
        "6_storage_batch_flush_us": round(p50_store, 3),
    }

    sum_of_stages_us = (
        p50_norm + ffi_bound_actual + c_logic_actual + py_wrapper_actual + p50_rec + p50_store
    )
    discrepancy_pct = abs(sum_of_stages_us - p50_total) / p50_total * 100.0 if p50_total > 0 else 0.0

    # Gate Decision: FFI boundary share of end-to-end budget
    ffi_share_pct = (ffi_bound_actual / p50_total) * 100.0 if p50_total > 0 else 0.0
    gate_passed_under_15 = ffi_share_pct < 15.0

    # Format cProfile top stats
    s_io = io.StringIO()
    ps = pstats.Stats(pr, stream=s_io).sort_stats("cumulative")
    ps.print_stats(30)
    cprofile_top = s_io.getvalue()

    result = {
        "benchmark": "profile_hotpath",
        "timestamp": time.time(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "events": events,
        "seed": seed,
        "throughput_eps": round(throughput_eps, 1),
        "latency_us": {
            "p50": round(p50_total, 3),
            "p95": round(p95_total, 3),
            "p99": round(p99_total, 3),
        },
        "stage_breakdown_p50_us": stage_breakdown,
        "sum_of_stages_us": round(sum_of_stages_us, 3),
        "sum_discrepancy_pct": round(discrepancy_pct, 2),
        "within_10_percent_acceptance": discrepancy_pct <= 10.0,
        "ffi_share_of_budget_pct": round(ffi_share_pct, 2),
        "gate": {
            "threshold_pct": 15.0,
            "measured_ffi_pct": round(ffi_share_pct, 2),
            "decision": "SKIP_PHASES_3_AND_4" if gate_passed_under_15 else "PROCEED_TO_PHASE_1_AND_4",
            "action": "Skip Phases 3-4 and jump to Phase 5" if gate_passed_under_15 else "Proceed with hot path fixes and C extension",
        },
        "cprofile_top_functions": cprofile_top[:2000],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Profile MDRAP V4 Hot Path Stages")
    parser.add_argument("--events", type=int, default=100_000, help="Event count (default 100,000)")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed (default 42)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--json", action="store_true", help="Output JSON directly")
    args = parser.parse_args()

    res = profile_pipeline(events=args.events, seed=args.seed)

    sum_us = res["sum_of_stages_us"]
    p50_us = res["latency_us"]["p50"]
    disc_pct = res["sum_discrepancy_pct"]
    gate = res["gate"]

    print("\n" + "=" * 78)
    print(f"MDRAP V4 Pipeline Cost Breakdown ({args.events:,} events, seed={args.seed})")
    print("=" * 78)
    print(f"{'Pipeline Stage':<45} | {'Cost (µs/event)':<15} | {'Share (%)':<10}")
    print("-" * 78)
    for name, cost in res["stage_breakdown_p50_us"].items():
        clean_name = name.split("_", 1)[1].replace("_us", "").replace("_", " ").title()
        share = (cost / p50_us) * 100.0 if p50_us > 0 else 0.0
        print(f"{clean_name:<45} | {cost:>11.3f} µs | {share:>8.1f}%")
    print("-" * 78)
    print(f"{'Sum of Measured Stages':<45} | {sum_us:>11.3f} µs | {(sum_us / p50_us)*100 if p50_us > 0 else 0:>8.1f}%")
    print(f"{'Measured End-to-End Latency (p50)':<45} | {p50_us:>11.3f} µs |   100.0%")
    print(f"{'Stage Sum Discrepancy':<45} | {disc_pct:>11.2f} %  |   [ACCEPTANCE: {'PASS' if disc_pct <= 10.0 else 'FAIL'}]")
    print("=" * 78)
    print(f"Throughput:                     {res['throughput_eps']:,.1f} events/sec")
    print(f"Net ctypes FFI Tax:             {res['stage_breakdown_p50_us']['2_ctypes_ffi_boundary_us']:.3f} µs ({res['ffi_share_of_budget_pct']:.1f}% of budget)")
    print(f"Pure C Quality Logic:           {res['stage_breakdown_p50_us']['3_c_side_quality_eval_us']:.3f} µs")
    print(f"Phase 0 Gate Threshold:         {gate['threshold_pct']:.1f}%")
    print(f"Gate Verdict:                   {gate['decision']} ({gate['action']})")
    print("=" * 78)

    # Save baseline JSON
    out_path = args.output
    if not out_path:
        date_tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(os.path.dirname(__file__), f"baseline_{date_tag}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    print(f"\n[+] Saved baseline profile results to: {out_path}")


if __name__ == "__main__":
    main()
