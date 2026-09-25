"""
Micro-benchmark: ctypes FFI Boundary Tax Deconstruction.

Deconstructs ctypes FFI call overhead across 4 calling conventions requested in Phase 9R.2:
  (i)   bare CDLL call with no argtypes (dynamic type inference)
  (ii)  CDLL call with pinned argtypes/restype (12 scalar arguments)
  (iii) CFUNCTYPE prototype function pointer (bypasses CDLL getattr dispatch)
  (iv)  Struct-by-reference (1 pointer argument vs 12 scalar arguments)
  (v)   Single scalar argument baseline (1 integer argument)
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from fastpath import _NATIVE_LIB, _CFastEvent, is_available


def run_micro_ffi(iterations: int = 2_000_000, runs: int = 5, warmup: int = 1) -> dict:
    if not is_available() or not _NATIVE_LIB:
        raise RuntimeError(
            "Native C library (_fastpath_native.dll/so) is not available."
        )

    dll_path = _NATIVE_LIB._name

    # 1. Variant (i): Bare CDLL with NO argtypes
    bare_lib = ctypes.CDLL(dll_path)
    bare_noop = bare_lib.fastpath_noop
    # Do not set bare_noop.argtypes or restype!

    # 2. Variant (ii): Pinned argtypes/restype (12 scalar args)
    pinned_lib = ctypes.CDLL(dll_path)
    pinned_noop = pinned_lib.fastpath_noop
    pinned_argtypes = [
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_int64,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
    ]
    pinned_noop.argtypes = pinned_argtypes
    pinned_noop.restype = ctypes.c_uint64

    # 3. Variant (iii): CFUNCTYPE prototype
    cfunc_type = ctypes.CFUNCTYPE(ctypes.c_uint64, *pinned_argtypes)
    proto_noop = cfunc_type(("fastpath_noop", pinned_lib))

    # 4. Variant (iv): Struct-by-reference (1 pointer arg)
    struct_noop = pinned_lib.fastpath_noop_struct
    struct_noop.argtypes = [ctypes.POINTER(_CFastEvent)]
    struct_noop.restype = ctypes.c_uint64

    c_ev = _CFastEvent()
    c_ev.source_id = 0
    c_ev.instrument_id = 0
    c_ev.event_type = 0
    c_ev.exchange_ts = 1.0
    c_ev.receive_ts = 1.0
    c_ev.sequence_num = 1
    c_ev.price = 100.0
    c_ev.quantity = 10.0
    c_ev.bid_price = 99.0
    c_ev.ask_price = 101.0
    c_ev.bid_size = 100.0
    c_ev.ask_size = 100.0
    c_ev_ref = ctypes.byref(c_ev)

    # 5. Variant (v): Single scalar arg baseline
    scalar1_noop = pinned_lib.fastpath_noop_scalar1
    scalar1_noop.argtypes = [ctypes.c_int64]
    scalar1_noop.restype = ctypes.c_uint64

    # Fastpath eval fast with pinned types
    eval_fast = pinned_lib.fastpath_eval_fast
    eval_fast.argtypes = pinned_argtypes
    eval_fast.restype = ctypes.c_uint64

    scalar_args = (0, 0, 0, 1.0, 1.0, 1, 100.0, 10.0, 99.0, 101.0, 100.0, 100.0)
    bare_args = (
        ctypes.c_int32(0),
        ctypes.c_int32(0),
        ctypes.c_int32(0),
        ctypes.c_double(1.0),
        ctypes.c_double(1.0),
        ctypes.c_int64(1),
        ctypes.c_double(100.0),
        ctypes.c_double(10.0),
        ctypes.c_double(99.0),
        ctypes.c_double(101.0),
        ctypes.c_double(100.0),
        ctypes.c_double(100.0),
    )

    # Warmup
    for _ in range(warmup):
        for _ in range(min(iterations, 50_000)):
            bare_noop(*bare_args)
            pinned_noop(*scalar_args)
            proto_noop(*scalar_args)
            struct_noop(c_ev_ref)
            scalar1_noop(1)
            eval_fast(*scalar_args)

    loop_samples = []
    bare_samples = []
    pinned_samples = []
    proto_samples = []
    struct_samples = []
    scalar1_samples = []
    eval_samples = []

    for run_idx in range(runs):
        # Python empty loop
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            pass
        t1 = time.perf_counter_ns()
        loop_samples.append((t1 - t0) / iterations)

        # (i) Bare CDLL (no argtypes)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            bare_noop(*bare_args)
        t1 = time.perf_counter_ns()
        bare_samples.append((t1 - t0) / iterations)

        # (ii) Pinned argtypes (12 scalar args)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            pinned_noop(*scalar_args)
        t1 = time.perf_counter_ns()
        pinned_samples.append((t1 - t0) / iterations)

        # (iii) CFUNCTYPE prototype
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            proto_noop(*scalar_args)
        t1 = time.perf_counter_ns()
        proto_samples.append((t1 - t0) / iterations)

        # (iv) Struct by pointer (1 arg)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            struct_noop(c_ev_ref)
        t1 = time.perf_counter_ns()
        struct_samples.append((t1 - t0) / iterations)

        # (v) Single scalar arg (1 int)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            scalar1_noop(1)
        t1 = time.perf_counter_ns()
        scalar1_samples.append((t1 - t0) / iterations)

        # Full fastpath_eval_fast (FFI + C logic)
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            eval_fast(*scalar_args)
        t1 = time.perf_counter_ns()
        eval_samples.append((t1 - t0) / iterations)

    def median(lst):
        s = sorted(lst)
        return s[len(s) // 2]

    loop_med = median(loop_samples)
    bare_med = median(bare_samples)
    pinned_med = median(pinned_samples)
    proto_med = median(proto_samples)
    struct_med = median(struct_samples)
    scalar1_med = median(scalar1_samples)
    eval_med = median(eval_samples)

    result = {
        "iterations": iterations,
        "runs": runs,
        "loop_baseline_ns": round(loop_med, 2),
        "variants": {
            "i_bare_no_argtypes_ns": round(bare_med - loop_med, 2),
            "ii_pinned_12_scalars_ns": round(pinned_med - loop_med, 2),
            "iii_cfunctype_prototype_ns": round(proto_med - loop_med, 2),
            "iv_struct_by_reference_ns": round(struct_med - loop_med, 2),
            "v_single_scalar_arg_ns": round(scalar1_med - loop_med, 2),
        },
        "full_eval_fast_ns": round(eval_med - loop_med, 2),
        "pure_c_quality_logic_ns": round(eval_med - pinned_med, 2),
    }
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Deconstruct ctypes FFI boundary overhead across 4 variants"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=2_000_000,
        help="Number of loop iterations (default 2M)",
    )
    parser.add_argument(
        "--runs", type=int, default=5, help="Number of timed runs (default 5)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    if not args.json:
        print(
            f"[*] Running micro_ffi deconstruction ({args.iterations:,} iterations x {args.runs} runs)...",
            flush=True,
        )

    res = run_micro_ffi(iterations=args.iterations, runs=args.runs)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("=" * 70)
        print("MDRAP ctypes FFI Deconstruction Results (Phase 9R.2)")
        print("=" * 70)
        print(
            f"Loop baseline (Python overhead):          {res['loop_baseline_ns']:>8.2f} ns"
        )
        print(
            f"(i)   Bare CDLL (no argtypes, 12 args):   {res['variants']['i_bare_no_argtypes_ns']:>8.2f} ns"
        )
        print(
            f"(ii)  Pinned CDLL (12 scalar args):       {res['variants']['ii_pinned_12_scalars_ns']:>8.2f} ns"
        )
        print(
            f"(iii) CFUNCTYPE prototype (12 args):      {res['variants']['iii_cfunctype_prototype_ns']:>8.2f} ns"
        )
        print(
            f"(iv)  Struct-by-pointer (1 arg):          {res['variants']['iv_struct_by_reference_ns']:>8.2f} ns"
        )
        print(
            f"(v)   Single scalar arg (1 int):          {res['variants']['v_single_scalar_arg_ns']:>8.2f} ns"
        )
        print("-" * 70)
        print(
            f"Full fastpath_eval_fast (FFI + C logic):  {res['full_eval_fast_ns']:>8.2f} ns"
        )
        print(
            f"Pure C quality logic (net of pinned FFI): {res['pure_c_quality_logic_ns']:>8.2f} ns"
        )
        print("=" * 70)
        delta_dynamic = (
            res["variants"]["i_bare_no_argtypes_ns"]
            - res["variants"]["ii_pinned_12_scalars_ns"]
        )
        print(f"\n[Audit Analysis]")
        print(f"  Dynamic arg inspection delta (i vs ii): {delta_dynamic:+.2f} ns")
        print(
            f"  Per-scalar-arg marshaling cost: ~{(res['variants']['ii_pinned_12_scalars_ns'] - res['variants']['v_single_scalar_arg_ns']) / 11:.2f} ns/arg"
        )
        print(
            f"  Speedup switching to struct-by-ref: {res['variants']['ii_pinned_12_scalars_ns'] / res['variants']['iv_struct_by_reference_ns']:.2f}x faster FFI boundary"
        )


if __name__ == "__main__":
    main()
