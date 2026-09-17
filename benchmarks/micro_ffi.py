"""
Micro-benchmark: ctypes FFI Boundary Tax.

Times 10,000,000 calls to fastpath_eval_fast in a tight loop,
subtracts the empty-C-function (fastpath_noop) baseline, and reports ns/call.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from fastpath import _NATIVE_LIB, is_available


def run_micro_ffi(iterations: int = 10_000_000, runs: int = 5, warmup: int = 1) -> dict:
    if not is_available() or not _NATIVE_LIB:
        raise RuntimeError("Native C library (_fastpath_native.dll/so) is not available.")

    eval_fast = _NATIVE_LIB.fastpath_eval_fast
    noop = getattr(_NATIVE_LIB, "fastpath_noop", None)

    if noop is None:
        raise RuntimeError("fastpath_noop export not found in native library.")

    args = (0, 0, 0, 1.0, 1.0, 1, 100.0, 10.0, 99.0, 101.0, 100.0, 100.0)

    # Warmup
    for _ in range(warmup):
        for _ in range(min(iterations, 100_000)):
            eval_fast(*args)
            noop(*args)

    eval_samples_ns = []
    noop_samples_ns = []
    loop_samples_ns = []

    for run_idx in range(runs):
        # 1. Baseline Python empty loop
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            pass
        t1 = time.perf_counter_ns()
        loop_samples_ns.append((t1 - t0) / iterations)

        # 2. Empty C function call via ctypes (measures pure FFI marshalling + call)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            noop(*args)
        t1 = time.perf_counter_ns()
        noop_samples_ns.append((t1 - t0) / iterations)

        # 3. Full fastpath_eval_fast via ctypes (measures FFI + C quality logic)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            eval_fast(*args)
        t1 = time.perf_counter_ns()
        eval_samples_ns.append((t1 - t0) / iterations)

    loop_median = sorted(loop_samples_ns)[len(loop_samples_ns) // 2]
    noop_median = sorted(noop_samples_ns)[len(noop_samples_ns) // 2]
    eval_median = sorted(eval_samples_ns)[len(eval_samples_ns) // 2]

    ffi_boundary_tax_ns = noop_median - loop_median
    c_logic_ns = eval_median - noop_median
    total_eval_call_ns = eval_median - loop_median

    result = {
        "iterations": iterations,
        "runs": runs,
        "loop_baseline_ns": round(loop_median, 2),
        "c_noop_raw_ns": round(noop_median, 2),
        "c_eval_raw_ns": round(eval_median, 2),
        "ffi_boundary_tax_ns": round(ffi_boundary_tax_ns, 2),
        "c_logic_ns": round(c_logic_ns, 2),
        "total_eval_call_ns": round(total_eval_call_ns, 2),
        "ffi_tax_percentage_of_eval": round((ffi_boundary_tax_ns / eval_median) * 100, 2) if eval_median > 0 else 0.0,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Measure ctypes FFI boundary overhead")
    parser.add_argument("--iterations", type=int, default=10_000_000, help="Number of loop iterations (default 10M)")
    parser.add_argument("--runs", type=int, default=5, help="Number of timed runs (default 5)")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    if not args.json:
        print(f"[*] Running micro_ffi benchmark with {args.iterations:,} iterations x {args.runs} runs...")

    res = run_micro_ffi(iterations=args.iterations, runs=args.runs)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("=" * 60)
        print("MDRAP ctypes FFI Micro-Benchmark Results")
        print("=" * 60)
        print(f"Iterations per run:          {res['iterations']:,}")
        print(f"Python empty loop:           {res['loop_baseline_ns']:.2f} ns/call")
        print(f"Empty C function (noop FFI): {res['c_noop_raw_ns']:.2f} ns/call")
        print(f"fastpath_eval_fast:          {res['c_eval_raw_ns']:.2f} ns/call")
        print("-" * 60)
        print(f"Net ctypes FFI boundary tax: {res['ffi_boundary_tax_ns']:.2f} ns/call")
        print(f"Pure C quality logic:        {res['c_logic_ns']:.2f} ns/call")
        print(f"FFI share of eval call:      {res['ffi_tax_percentage_of_eval']:.1f}%")
        print("=" * 60)


if __name__ == "__main__":
    main()
