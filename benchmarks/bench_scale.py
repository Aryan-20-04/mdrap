"""MDRAP Program I-8: Scaled Universe Benchmark Suite.

Evaluates single-node validation and normalization latency across varying instrument universe
scales: 24 (small portfolio), 1,000 (standard equities book), and 10,000 (broad institutional universe).

Measures p50, p90, p95, p99, p99.9, max latency, memory footprint, and throughput.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from mdrap.engine import Engine, EngineConfig
from mdrap.models import RawEvent
from mdrap.clock import FixedClock


def run_scale_benchmark(
    scales: list[int] | None = None,
    events_per_scale: int = 30_000,
) -> dict[str, Any]:
    """Execute scaled universe benchmark across 24, 1000, and 10000 instruments."""
    if scales is None:
        scales = [24, 1_000, 10_000]

    report: dict[str, Any] = {
        "suite": "MDRAP Scaled Universe Benchmark (Program I-8)",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events_per_scale": events_per_scale,
        "results": {},
    }

    clock = FixedClock(1700000000.0)

    for scale in scales:
        universe = [f"INST_{i:05d}" for i in range(scale)]
        latencies_ns: list[int] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = EngineConfig(
                wal_dir=tmp_dir,
                clock=clock,
                fsync_policy="never",  # Focus purely on engine state transition latency
            )
            engine = Engine.open(tmp_dir, config=cfg)
            try:
                # Warm-up phase: populate universe state slots
                for sym in universe:
                    clock.advance(0.001)
                    engine.submit(
                        RawEvent(
                            source="FEED",
                            payload={
                                "instrument": sym,
                                "event_type": "TRADE",
                                "sequence": 1,
                                "price": 100.0,
                                "quantity": 10.0,
                                "exchange_ts": clock.now(),
                            },
                        )
                    )

                tracemalloc.start()
                mem_start = tracemalloc.get_traced_memory()[0]

                t_start = time.perf_counter()

                # Timed phase
                for i in range(events_per_scale):
                    sym = universe[i % scale]
                    clock.advance(0.0001)
                    now_ts = clock.now()
                    raw = RawEvent(
                        source="FEED",
                        receive_timestamp=now_ts,
                        payload={
                            "instrument": sym,
                            "event_type": "TRADE",
                            "sequence": 2 + (i // scale),
                            "price": 100.0 + (i % 50) * 0.05,
                            "quantity": 10.0,
                            "exchange_ts": now_ts,
                        },
                    )
                    t0 = time.perf_counter_ns()
                    engine.submit(raw)
                    t1 = time.perf_counter_ns()
                    latencies_ns.append(t1 - t0)

                t_end = time.perf_counter()
                mem_end = tracemalloc.get_traced_memory()[0]
                tracemalloc.stop()
            finally:
                engine.close()

        elapsed_s = t_end - t_start
        throughput = events_per_scale / elapsed_s if elapsed_s > 0 else 0.0

        latencies_us = [l / 1000.0 for l in sorted(latencies_ns)]
        n = len(latencies_us)

        scale_res = {
            "universe_size": scale,
            "events_measured": events_per_scale,
            "elapsed_seconds": round(elapsed_s, 4),
            "throughput_eps": round(throughput, 1),
            "latency_us": {
                "p50": round(latencies_us[int(n * 0.50)], 2),
                "p90": round(latencies_us[int(n * 0.90)], 2),
                "p95": round(latencies_us[int(n * 0.95)], 2),
                "p99": round(latencies_us[int(n * 0.99)], 2),
                "p999": round(latencies_us[int(n * 0.999)], 2),
                "max": round(latencies_us[-1], 2),
                "mean": round(sum(latencies_us) / n, 2),
            },
            "memory_delta_kb": round((mem_end - mem_start) / 1024.0, 2),
        }
        report["results"][str(scale)] = scale_res

    return report


def print_report(report: dict[str, Any]) -> None:
    """Print clean ASCII table of benchmark results."""
    print("=" * 82)
    print(f" {report['suite']} ")
    print(f" Timestamp: {report['timestamp']} | Events / scale: {report['events_per_scale']:,}")
    print("=" * 82)
    header = f"{'Universe':<12} {'Throughput':<16} {'p50 (µs)':<10} {'p95 (µs)':<10} {'p99 (µs)':<10} {'Max (µs)':<10} {'Mem (KB)':<10}"
    print(header)
    print("-" * 82)
    for scale_key, res in report["results"].items():
        u = res["universe_size"]
        tp = f"{res['throughput_eps']:,.1f} eps"
        lat = res["latency_us"]
        mem = f"{res['memory_delta_kb']:,.1f}"
        print(
            f"{u:<12} {tp:<16} {lat['p50']:<10.2f} {lat['p95']:<10.2f} {lat['p99']:<10.2f} {lat['max']:<10.2f} {mem:<10}"
        )
    print("=" * 82)


def main() -> int:
    parser = argparse.ArgumentParser(description="MDRAP Scaled Universe Benchmark Harness")
    parser.add_argument(
        "-e",
        "--events",
        type=int,
        default=30_000,
        help="Number of events measured per universe scale (default: 30,000)",
    )
    parser.add_argument(
        "--scales",
        type=int,
        nargs="+",
        default=[24, 1_000, 10_000],
        help="Instrument universe sizes to evaluate (default: 24 1000 10000)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON results",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Save JSON results to specified file path",
    )
    args = parser.parse_args()

    report = run_scale_benchmark(scales=args.scales, events_per_scale=args.events)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
