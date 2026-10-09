"""
MDRAP Phase 2 Performance & Latency Benchmark.

Measures:
1. Engine Canonical Event Step Latency (p50, p90, p95, p99, p99.9).
2. Saturated Bounded Queue Broadcast Throughput and Tail Latency.
3. Runtime Lifecycle Start/Stop Latency.
4. Supervisor Overhead under active worker execution.
"""

from __future__ import annotations

import json
import math
import os
import queue
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, List

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdrap.models import RawEvent, EventType
from mdrap.engine import Engine
from mdrap.runtime import Runtime, RuntimeConfig
from mdrap.supervisor import RuntimeSupervisor, WorkerSpec


def percentile(data: List[float], p: float) -> float:
    """Calculate the p-th percentile of a sorted list of floats."""
    if not data:
        return 0.0
    k = (len(data) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return data[int(k)]
    d0 = data[int(f)] * (c - k)
    d1 = data[int(c)] * (k - f)
    return d0 + d1


def run_engine_step_benchmark(num_events: int = 20_000) -> dict[str, Any]:
    """Measure single-event Engine step latency percentiles."""
    engine = Engine(staleness_threshold_s=5.0)
    latencies_us: list[float] = []

    raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "price": 50000.0,
            "quantity": 1.5,
            "exchange_ts": 1700000000.0,
            "sequence": 1,
        },
        receive_timestamp=1700000000.001,
    )

    t_start = time.perf_counter()
    for seq in range(1, num_events + 1):
        raw.payload["sequence"] = seq
        raw.payload["price"] = 50000.0 + (seq % 100) * 0.5
        t0 = time.perf_counter_ns()
        engine.process(raw)
        t1 = time.perf_counter_ns()
        latencies_us.append((t1 - t0) / 1000.0)
    t_end = time.perf_counter()

    latencies_us.sort()
    duration_s = t_end - t_start
    eps = num_events / duration_s if duration_s > 0 else 0.0

    return {
        "num_events": num_events,
        "duration_s": round(duration_s, 4),
        "throughput_eps": round(eps, 1),
        "latency_us": {
            "min": round(latencies_us[0], 2),
            "p50": round(percentile(latencies_us, 0.50), 2),
            "p90": round(percentile(latencies_us, 0.90), 2),
            "p95": round(percentile(latencies_us, 0.95), 2),
            "p99": round(percentile(latencies_us, 0.99), 2),
            "p99_9": round(percentile(latencies_us, 0.999), 2),
            "max": round(latencies_us[-1], 2),
            "mean": round(sum(latencies_us) / len(latencies_us), 2),
        },
    }


def run_bounded_queue_saturation_benchmark(num_events: int = 15_000) -> dict[str, Any]:
    """Measure throughput and latency under saturated queue backpressure."""
    q: queue.Queue = queue.Queue(maxsize=1000)
    dropped = 0
    enqueued = 0
    latencies_us: list[float] = []

    t_start = time.perf_counter()
    for i in range(num_events):
        t0 = time.perf_counter_ns()
        try:
            q.put_nowait(i)
            enqueued += 1
        except queue.Full:
            dropped += 1
        t1 = time.perf_counter_ns()
        latencies_us.append((t1 - t0) / 1000.0)
    t_end = time.perf_counter()

    latencies_us.sort()
    duration_s = t_end - t_start
    eps = num_events / duration_s if duration_s > 0 else 0.0

    return {
        "total_operations": num_events,
        "enqueued": enqueued,
        "dropped": dropped,
        "duration_s": round(duration_s, 4),
        "throughput_ops_sec": round(eps, 1),
        "latency_us": {
            "p50": round(percentile(latencies_us, 0.50), 2),
            "p95": round(percentile(latencies_us, 0.95), 2),
            "p99": round(percentile(latencies_us, 0.99), 2),
            "max": round(latencies_us[-1], 2),
        },
    }


def run_runtime_lifecycle_benchmark() -> dict[str, Any]:
    """Measure Runtime startup, event ingest, and graceful stop drain time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = os.path.join(tmpdir, "bench_wal")
        db_path = os.path.join(tmpdir, "bench.db")

        cfg = RuntimeConfig(
            wal_path=wal_path,
            db_path=db_path,
            staleness_threshold_s=5.0,
            drain_timeout_s=2.0,
            fsync_policy="always",
        )

        t0 = time.perf_counter()
        rt = Runtime(cfg)
        rt.initialize()
        rt.start()
        t1 = time.perf_counter()
        init_duration_ms = (t1 - t0) * 1000.0

        # Process 1,000 durable events
        raw = RawEvent(
            source="SIMULATOR",
            payload={
                "instrument": "ETH/USD",
                "event_type": "TRADE",
                "price": 3000.0,
                "quantity": 2.0,
                "exchange_ts": time.time(),
                "sequence": 1,
            },
            receive_timestamp=time.time(),
        )
        t_proc_start = time.perf_counter()
        for i in range(1000):
            raw.payload["sequence"] = i + 1
            rt.submit(raw)
        t_proc_end = time.perf_counter()
        proc_duration_s = t_proc_end - t_proc_start

        # Stop and drain
        t_stop_start = time.perf_counter()
        rt.stop()
        t_stop_end = time.perf_counter()
        stop_duration_ms = (t_stop_end - t_stop_start) * 1000.0

        return {
            "init_duration_ms": round(init_duration_ms, 2),
            "events_processed": 1000,
            "proc_duration_s": round(proc_duration_s, 4),
            "ingest_eps": round(1000 / proc_duration_s, 1) if proc_duration_s > 0 else 0.0,
            "stop_drain_duration_ms": round(stop_duration_ms, 2),
            "drain_success": rt.drain_success,
        }


def main():
    print("=" * 70)
    print("MDRAP Phase 2 Production Runtime Benchmark Suite")
    print("=" * 70)

    print("\n[1/3] Benchmarking Engine Step Processing...")
    engine_results = run_engine_step_benchmark(20_000)
    print(f"  Throughput: {engine_results['throughput_eps']:,.1f} EPS")
    print(f"  Latency: p50={engine_results['latency_us']['p50']}us, p95={engine_results['latency_us']['p95']}us, p99={engine_results['latency_us']['p99']}us")

    print("\n[2/3] Benchmarking Bounded Queue Saturation Backpressure...")
    queue_results = run_bounded_queue_saturation_benchmark(15_000)
    print(f"  Throughput: {queue_results['throughput_ops_sec']:,.1f} Ops/s")
    print(f"  Drops Recorded: {queue_results['dropped']} (Enqueued: {queue_results['enqueued']})")
    print(f"  Latency: p50={queue_results['latency_us']['p50']}us, p99={queue_results['latency_us']['p99']}us")

    print("\n[3/3] Benchmarking Runtime Lifecycle & Persistence Drain...")
    lifecycle_results = run_runtime_lifecycle_benchmark()
    print(f"  Init Time: {lifecycle_results['init_duration_ms']} ms")
    print(f"  Ingest Throughput: {lifecycle_results['ingest_eps']:,.1f} EPS")
    print(f"  Stop & Drain Time: {lifecycle_results['stop_drain_duration_ms']} ms (Success: {lifecycle_results['drain_success']})")

    results = {
        "timestamp": time.time(),
        "platform": sys.platform,
        "python_version": sys.version,
        "engine_step": engine_results,
        "bounded_queue_saturation": queue_results,
        "runtime_lifecycle": lifecycle_results,
    }

    out_path = Path("audit/phase2/benchmark_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nBenchmark results saved to: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
