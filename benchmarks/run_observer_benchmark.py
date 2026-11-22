"""
MDRAP Full-System Benchmark Suite - Phase 4: Observer Sidecar Benchmark
Measures Observer state processing throughput, exporter throughput, JSONL write latency,
queue saturation drop accounting, and multi-venue (5 venues) multi-symbol (4 assets) scaling.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List

# Ensure paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "external_sidecar", "mdrap_observer")))
from observer.state import ObserverState, VENUES, CORE_ASSETS
from observer.exporter import ObserverExporter


def get_rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def benchmark_observer_state_processing(num_events: int = 100_000) -> Dict[str, Any]:
    state = ObserverState()
    venues = VENUES
    symbols = ["BTCUSDT", "BTC-USD", "ETHUSDT", "ETH-USD"]
    
    # Pre-generate synthetic events
    events = []
    for i in range(1, num_events + 1):
        venue = venues[i % len(venues)]
        symbol = symbols[i % len(symbols)]
        base_price = 65000.0 if "BTC" in symbol else 3400.0
        p = base_price + (i % 200) * 0.25
        events.append({
            "type": "TICK",
            "seq": i,
            "feed_seq": (i // len(venues)) + 1,
            "venue": venue,
            "source": venue,
            "sym": symbol,
            "p": p,
            "q": 1.25,
            "bid": p - 0.5,
            "ask": p + 0.5,
            "bid_size": 5.0,
            "ask_size": 4.0,
            "ts": time.time(),
        })

    rss_start = get_rss_mb()
    t_cpu_start = time.process_time()
    latencies_ns = []

    t0 = time.perf_counter()
    for ev in events:
        t_before = time.perf_counter_ns()
        state.process_event(ev)
        latencies_ns.append(time.perf_counter_ns() - t_before)
    elapsed = time.perf_counter() - t0

    t_cpu = time.process_time() - t_cpu_start
    rss_end = get_rss_mb()

    latencies_ns.sort()
    n = len(latencies_ns) or 1
    lat_us = [lat / 1000.0 for lat in latencies_ns]

    return {
        "num_events": num_events,
        "elapsed_s": round(elapsed, 4),
        "throughput_eps": round(num_events / elapsed, 1),
        "cpu_time_s": round(t_cpu, 3),
        "latency_us": {
            "p50": round(lat_us[int(n * 0.50)], 2),
            "p90": round(lat_us[int(n * 0.90)], 2),
            "p95": round(lat_us[int(n * 0.95)], 2),
            "p99": round(lat_us[int(n * 0.99)], 2),
            "p99_9": round(lat_us[int(n * 0.999)], 2),
            "max": round(lat_us[-1], 2),
        },
        "rss_mb": {
            "start": round(rss_start, 2),
            "end": round(rss_end, 2),
            "growth": round(rss_end - rss_start, 2),
        },
        "state_summary": {
            "total_events": state.stream_health.events_received,
            "active_venues": len(state.health),
            "active_symbols": len(state.quotes),
            "total_stream_gaps": state.stream_health.gaps,
            "total_feed_gaps": sum(v.feed_gaps for v in state.health.values()),
            "total_feed_duplicates": sum(v.feed_duplicates for v in state.health.values()),
            "total_feed_out_of_order": sum(v.feed_out_of_order for v in state.health.values()),
        }
    }


def benchmark_observer_exporter(num_records: int = 50_000) -> Dict[str, Any]:
    tmpdir = tempfile.mkdtemp(prefix="obs_bench_exp_")
    exporter = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.2, queue_size=100000)
    exporter.start()

    rss_start = get_rss_mb()
    t_cpu_start = time.process_time()

    sample_event = {
        "type": "TICK",
        "seq": 100,
        "venue": "BINANCE",
        "sym": "BTCUSDT",
        "p": 65120.50,
        "q": 2.1,
        "bid": 65120.00,
        "ask": 65121.00,
        "ts": time.time(),
    }

    t0 = time.perf_counter()
    for _ in range(num_records):
        exporter.record_event(sample_event)
    t_enqueue = time.perf_counter() - t0

    # Wait for background queue to drain
    t_drain_start = time.perf_counter()
    while not exporter._queue.empty():
        time.sleep(0.01)
    exporter.stop()
    elapsed_total = time.perf_counter() - t0

    t_cpu = time.process_time() - t_cpu_start
    rss_end = get_rss_mb()

    # Measure file sizes
    events_sz = os.path.getsize(exporter.events_file) if os.path.exists(exporter.events_file) else 0

    shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "num_records": num_records,
        "enqueue_elapsed_s": round(t_enqueue, 4),
        "enqueue_eps": round(num_records / t_enqueue, 1),
        "total_flush_elapsed_s": round(elapsed_total, 4),
        "drain_throughput_eps": round(num_records / elapsed_total, 1),
        "events_written": exporter.events_written,
        "dropped_records": exporter.dropped_records,
        "write_errors": exporter.write_errors,
        "file_size_bytes": events_sz,
        "avg_bytes_per_record": round(events_sz / num_records, 1) if num_records > 0 else 0,
        "cpu_time_s": round(t_cpu, 3),
        "rss_mb": {
            "start": round(rss_start, 2),
            "end": round(rss_end, 2),
            "growth": round(rss_end - rss_start, 2),
        }
    }


def benchmark_queue_saturation() -> Dict[str, Any]:
    """Test drop accounting when queue saturates."""
    tmpdir = tempfile.mkdtemp(prefix="obs_bench_sat_")
    # Small queue of 2,000 slots
    queue_cap = 2_000
    burst_count = 10_000
    exporter = ObserverExporter(output_dir=tmpdir, queue_size=queue_cap)
    exporter.start()

    sample_event = {"seq": 1, "sym": "BTCUSDT", "p": 65000.0}

    # Burst 10,000 events without pause
    for _ in range(burst_count):
        exporter.record_event(sample_event)

    time.sleep(0.2)
    exporter.stop()

    dropped = exporter.dropped_records
    written = exporter.events_written
    shutil.rmtree(tmpdir, ignore_errors=True)

    return {
        "queue_capacity": queue_cap,
        "burst_count": burst_count,
        "events_written": written,
        "dropped_records": dropped,
        "sum_accounted": written + dropped,
        "accounting_accurate": (written + dropped == burst_count),
    }


def main():
    print("=" * 80)
    print(" MDRAP v6.0.8 Observer Sidecar Benchmark")
    print("=" * 80)

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": sys.platform,
        "python": sys.version,
    }

    print("\n[1/3] Benchmarking Observer In-Memory State Engine (5 venues x 4 assets)...")
    state_res = benchmark_observer_state_processing(100_000)
    results["state_processing"] = state_res
    print(f"  Processed {state_res['num_events']:,} events in {state_res['elapsed_s']:.3f}s: {state_res['throughput_eps']:,.1f} EPS")
    print(f"  Latency: p50={state_res['latency_us']['p50']:.1f}us | p95={state_res['latency_us']['p95']:.1f}us | p99={state_res['latency_us']['p99']:.1f}us | max={state_res['latency_us']['max']:.1f}us")
    print(f"  Memory Growth: {state_res['rss_mb']['growth']:.2f} MB | CPU: {state_res['cpu_time_s']:.2f}s")

    print("\n[2/3] Benchmarking Observer Async Exporter (JSONL serialization & flush)...")
    exp_res = benchmark_observer_exporter(50_000)
    results["exporter"] = exp_res
    print(f"  Enqueue Rate:    {exp_res['enqueue_eps']:>10,.1f} records/sec ({exp_res['enqueue_elapsed_s']:.3f}s)")
    print(f"  Disk Drain Rate: {exp_res['drain_throughput_eps']:>10,.1f} records/sec ({exp_res['total_flush_elapsed_s']:.3f}s)")
    print(f"  Written: {exp_res['events_written']:,} | Dropped: {exp_res['dropped_records']} | Errors: {exp_res['write_errors']}")
    print(f"  File Size: {exp_res['file_size_bytes'] / (1024*1024):.2f} MB ({exp_res['avg_bytes_per_record']} bytes/record)")

    print("\n[3/3] Benchmarking Queue Saturation & Drop Accounting...")
    sat_res = benchmark_queue_saturation()
    results["queue_saturation"] = sat_res
    print(f"  Burst: {sat_res['burst_count']:,} -> Written: {sat_res['events_written']:,} | Dropped: {sat_res['dropped_records']:,}")
    print(f"  Drop Accounting Accurate: {sat_res['accounting_accurate']} (Sum: {sat_res['sum_accounted']:,})")

    out_file = os.path.join("audit", "benchmark", "raw", "observer_benchmark.json")
    with open(out_file, "w") as fp:
        json.dump(results, fp, indent=2)
    print(f"\nAll observer benchmark results saved to {out_file}")


if __name__ == "__main__":
    main()
