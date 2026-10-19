"""MDRAP Relative Performance Regression Gate (Item 5.5).

Measures engine throughput and tail latency percentiles against committed
performance baselines. Fails with non-zero exit code if performance regresses
beyond configurable tolerance thresholds.

Usage:
    python benchmarks/check_regression.py [--tolerance-pct 15.0] [--record]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# Ensure project src/ is on sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

from mdrap.clock import FixedClock
from mdrap.engine import Engine
from mdrap.models import RawEvent


DEFAULT_BASELINE_FILE = os.path.join(
    os.path.dirname(__file__), "baseline_performance.json"
)

# Standard baseline metrics on standard reference hardware (1 CPU core)
DEFAULT_BASELINE = {
    "engine_throughput_eps": 55_000.0,
    "latency_p50_us": 15.0,
    "latency_p95_us": 35.0,
    "latency_p99_us": 65.0,
}


def generate_benchmark_events(num_events: int = 50_000) -> list[RawEvent]:
    """Generate deterministic raw market data events for benchmark evaluation."""
    events: list[RawEvent] = []
    base_price = 100.0
    ts_start = 1700000000.0

    for i in range(num_events):
        p_delta = ((i % 100) - 50) * 0.01
        px = round(base_price + p_delta, 2)
        raw = RawEvent(
            source="BENCH_SRC",
            receive_timestamp=ts_start + (i * 0.0001),
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE" if i % 2 == 0 else "QUOTE",
                "exchange_ts": ts_start + (i * 0.0001),
                "sequence": i + 1,
                "price": px,
                "quantity": 100.0,
                "bid": round(px - 0.05, 2),
                "ask": round(px + 0.05, 2),
            },
        )
        events.append(raw)
    return events


def run_benchmark(num_events: int = 50_000) -> dict[str, float]:
    """Execute timed engine run and measure throughput and latency percentiles."""
    events = generate_benchmark_events(num_events)
    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    latencies_us: list[float] = []

    # Warmup
    for ev in events[:2000]:
        state, _ = engine.step(state, ev, clock, offset=0)

    # Timed run
    start_time = time.perf_counter()
    for ev in events:
        t0 = time.perf_counter_ns()
        state, _ = engine.step(state, ev, clock, offset=0)
        t1 = time.perf_counter_ns()
        latencies_us.append((t1 - t0) / 1000.0)
    elapsed = time.perf_counter() - start_time

    throughput_eps = len(events) / elapsed if elapsed > 0 else 0.0
    latencies_us.sort()
    n = len(latencies_us)

    p50 = latencies_us[int(n * 0.50)]
    p95 = latencies_us[int(n * 0.95)]
    p99 = latencies_us[int(n * 0.99)]

    return {
        "engine_throughput_eps": round(throughput_eps, 1),
        "latency_p50_us": round(p50, 2),
        "latency_p95_us": round(p95, 2),
        "latency_p99_us": round(p99, 2),
    }


def compare_and_gate(
    current: dict[str, float],
    baseline: dict[str, float],
    tolerance_pct: float = 20.0,
) -> tuple[bool, list[str]]:
    """Compare measured metrics against baseline with relative percentage tolerance.

    Returns:
        (passed, report_lines)
    """
    passed = True
    report = []
    report.append(
        f"{'Metric':<25} | {'Measured':<12} | {'Baseline':<12} | {'Delta':<10} | {'Status'}"
    )
    report.append("-" * 75)

    # 1. Throughput check (higher is better)
    cur_tp = current["engine_throughput_eps"]
    base_tp = baseline.get(
        "engine_throughput_eps", DEFAULT_BASELINE["engine_throughput_eps"]
    )
    min_tp = base_tp * (1.0 - (tolerance_pct / 100.0))
    tp_delta_pct = ((cur_tp - base_tp) / base_tp) * 100.0
    tp_ok = cur_tp >= min_tp
    if not tp_ok:
        passed = False
    status_str = "PASS" if tp_ok else f"REGRESSION (>{tolerance_pct:.0f}%)"
    report.append(
        f"{'Throughput (EPS)':<25} | {cur_tp:<12.1f} | {base_tp:<12.1f} | {tp_delta_pct:>+8.1f}% | {status_str}"
    )

    # 2. Latency checks (lower is better)
    for p_name, key in [
        ("Latency p50 (us)", "latency_p50_us"),
        ("Latency p95 (us)", "latency_p95_us"),
        ("Latency p99 (us)", "latency_p99_us"),
    ]:
        cur_lat = current[key]
        base_lat = baseline.get(key, DEFAULT_BASELINE[key])
        max_lat = base_lat * (1.0 + (tolerance_pct / 100.0))
        lat_delta_pct = ((cur_lat - base_lat) / base_lat) * 100.0
        lat_ok = cur_lat <= max_lat
        if not lat_ok:
            passed = False
        st = "PASS" if lat_ok else f"REGRESSION (>{tolerance_pct:.0f}%)"
        report.append(
            f"{p_name:<25} | {cur_lat:<12.2f} | {base_lat:<12.2f} | {lat_delta_pct:>+8.1f}% | {st}"
        )

    return passed, report


def main() -> int:
    parser = argparse.ArgumentParser(description="MDRAP Benchmark Regression Gate")
    parser.add_argument(
        "--tolerance-pct",
        type=float,
        default=25.0,
        help="Allowable regression percentage before gate failure",
    )
    parser.add_argument(
        "--baseline-file",
        default=DEFAULT_BASELINE_FILE,
        help="Path to committed baseline metrics JSON",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Record current run as the new baseline JSON",
    )
    parser.add_argument(
        "--events", type=int, default=30_000, help="Number of benchmark events"
    )
    args = parser.parse_args()

    print(f"[*] Running MDRAP performance benchmark ({args.events} events)...")
    current_metrics = run_benchmark(num_events=args.events)

    if args.record:
        with open(args.baseline_file, "w", encoding="utf-8") as f:
            json.dump(current_metrics, f, indent=2)
        print(f"[+] Saved baseline metrics to: {args.baseline_file}")
        print(json.dumps(current_metrics, indent=2))
        return 0

    baseline = DEFAULT_BASELINE.copy()
    if os.path.exists(args.baseline_file):
        try:
            with open(args.baseline_file, "r", encoding="utf-8") as f:
                baseline.update(json.load(f))
        except Exception as exc:
            print(
                f"[!] Warning: Failed to load baseline file ({exc}), using default fallback."
            )

    passed, report_lines = compare_and_gate(
        current=current_metrics,
        baseline=baseline,
        tolerance_pct=args.tolerance_pct,
    )

    print("\n" + "=" * 75)
    print("MDRAP BENCHMARK REGRESSION GATE SCORECARD")
    print("=" * 75)
    for line in report_lines:
        print(line)
    print("=" * 75)

    if passed:
        print("[PASS] Performance verification passed within tolerance.")
        return 0
    else:
        print(
            "[FAIL] Performance regression detected beyond allowable tolerance threshold."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
