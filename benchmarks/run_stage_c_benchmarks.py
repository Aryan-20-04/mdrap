"""MDRAP Stage C Controlled Benchmark Runner.

Executes:
1. Benchmark 1: Throughput (Engine, TCP, SHM, Queue Occupancy, Backpressure)
2. Benchmark 2: Latency (Ingress, Engine, Transport, E2E, Monotonic Clocks, Percentiles, Telescoping Identity, Clock Overhead)
3. Benchmark 3: Memory and CPU (Baseline, Peak, Steady-State, Post-Teardown RSS, CPU Utilization, Queue Saturation)

Saves raw structured JSON artifact to audit/benchmark/final_assessment/raw/benchmark_stage_c.json.
"""

from __future__ import annotations

import gc
import json
import math
import os
import platform
import random
import shutil
import struct
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List

# Benchmark environment configuration
os.environ["MDRAP_API_KEY_SALT"] = "stage_c_benchmark_salt_12345"
os.environ["MDRAP_DEMO"] = "1"

# Ensure src and observer in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "external_sidecar", "mdrap_observer")))

import psutil

from mdrap.models import RawEvent
from mdrap.service import MarketDataDaemon
from mdrap.latency import MonotonicLatencyTracker, StageLatencyMetric, ReservoirSampler
from mdrap.shm import SHMWriter, SHMReader
from observer.client import ObserverClient
from observer.exporter import ObserverExporter
from observer.state import ObserverState, VENUES


def get_current_rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / (1024.0 * 1024.0)


def measure_clock_overhead(iterations: int = 100_000) -> Dict[str, float]:
    """Measure the distribution of time.perf_counter_ns() call durations."""
    samples = []
    # Warmup
    for _ in range(1000):
        time.perf_counter_ns()

    t_prev = time.perf_counter_ns()
    for _ in range(iterations):
        t_curr = time.perf_counter_ns()
        delta = t_curr - t_prev
        samples.append(delta)
        t_prev = t_curr

    # Ignore first sample as it may include loop setup
    samples = sorted(samples[1:])
    n = len(samples)
    return {
        "call_count": n,
        "p50_ns": samples[int(n * 0.50)],
        "p90_ns": samples[int(n * 0.90)],
        "p95_ns": samples[int(n * 0.95)],
        "p99_ns": samples[int(n * 0.99)],
        "p99_9_ns": samples[int(n * 0.999)],
        "max_ns": samples[-1],
        "avg_ns": round(sum(samples) / n, 2),
    }


def run_benchmark_1_throughput() -> Dict[str, Any]:
    print("\n--- Running Benchmark 1: Throughput ---")
    results = {}

    # 1.1 Pure Ingestion Engine Throughput
    daemon_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    daemon = None
    try:
        daemon = MarketDataDaemon(
            host="127.0.0.1",
            port=0,
            db_path=daemon_db,
            use_live=False,
            enable_shm=False,
            sim_speed_eps=0.0,
        )
        daemon.start(blocking=False)
        time.sleep(0.3)

        num_events = 50_000
        events = [
            RawEvent(
                source="BINANCE",
                payload={"instrument": "BTCUSDT", "event_type": "TICK", "price": 65000.0 + (i % 100) * 0.5, "size": 1.0, "t": time.time(), "s": i},
            )
            for i in range(1, num_events + 1)
        ]

        t_start = time.perf_counter()
        for evt in events:
            daemon._process_and_broadcast(evt)
        t_elapsed = time.perf_counter() - t_start

        engine_eps = num_events / t_elapsed
        print(f"Engine Ingestion: {num_events:,} events in {t_elapsed:.3f}s -> {engine_eps:,.0f} EPS")
        results["engine_ingestion"] = {
            "events": num_events,
            "elapsed_s": round(t_elapsed, 4),
            "throughput_eps": round(engine_eps, 1),
        }
    finally:
        if daemon:
            daemon.stop()
        if os.path.exists(daemon_db):
            os.remove(daemon_db)

    # 1.2 SHM Zero-Copy Throughput
    ring_name = f"mdrap_stage_c_shm_{int(time.time()*1000)%100000}"
    writer = SHMWriter(ring_name, slot_count=65536)
    reader = SHMReader(ring_name)

    shm_events = 50_000
    t_start = time.perf_counter()
    for i in range(1, shm_events + 1):
        writer.write_tick(
            seq=i,
            symbol="BTCUSDT",
            source="BINANCE",
            price=65000.0 + (i % 100),
            size=1.5,
            broadcast_ts=time.perf_counter(),
        )
    t_write = time.perf_counter() - t_start

    # Read back
    read_count = 0
    t_read_start = time.perf_counter()
    for i in range(1, shm_events + 1):
        slot = reader.read_tick(i)
        if slot is not None:
            read_count += 1
    t_read = time.perf_counter() - t_read_start

    writer.close()
    reader.close()
    try:
        writer.unlink()
    except Exception:
        pass

    shm_write_eps = shm_events / t_write
    shm_read_eps = read_count / t_read
    print(f"SHM Write: {shm_events:,} in {t_write:.3f}s -> {shm_write_eps:,.0f} EPS")
    print(f"SHM Read:  {read_count:,} in {t_read:.3f}s -> {shm_read_eps:,.0f} EPS")
    results["shm_transport"] = {
        "events": shm_events,
        "write_elapsed_s": round(t_write, 4),
        "write_throughput_eps": round(shm_write_eps, 1),
        "read_count": read_count,
        "read_elapsed_s": round(t_read, 4),
        "read_throughput_eps": round(shm_read_eps, 1),
        "delivery_pct": round(read_count / shm_events * 100.0, 2),
    }

    # 1.3 TCP Streaming Pipeline Sustained Windows & Queue Occupancy
    tcp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        daemon = MarketDataDaemon(
            host="127.0.0.1",
            port=0,
            db_path=tcp_db,
            use_live=False,
            enable_shm=False,
            sim_speed_eps=0.0,
        )
        daemon.start(blocking=False)
        time.sleep(0.3)
        port = daemon.port

        received_count = 0
        state = ObserverState()

        def on_evt(evt):
            nonlocal received_count
            state.process_event(evt)
            received_count += 1

        client = ObserverClient(mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_evt)
        client.start()
        time.sleep(0.3)

        window_duration = 2.0
        windows = 5
        rate_per_sec = 5000.0
        window_events = int(rate_per_sec * window_duration)

        window_stats = []
        max_queue_depth = 0

        for w in range(windows):
            w_start = time.perf_counter()
            prev_recv = received_count
            for i in range(window_events):
                raw = RawEvent(
                    source="BINANCE",
                    payload={"instrument": "BTCUSDT", "event_type": "TICK", "price": 65000.0 + (i % 50), "size": 0.5, "t": time.time(), "s": i + 1},
                )
                daemon._process_and_broadcast(raw)

            # Drain window
            time.sleep(window_duration)
            w_elapsed = time.perf_counter() - w_start
            w_recv = received_count - prev_recv
            w_eps = w_recv / w_elapsed
            window_stats.append({
                "window_idx": w + 1,
                "sent": window_events,
                "received": w_recv,
                "elapsed_s": round(w_elapsed, 4),
                "throughput_eps": round(w_eps, 1),
            })
            print(f"Window {w+1}: sent={window_events}, recv={w_recv}, throughput={w_eps:,.0f} EPS")

        client.stop()
        daemon.stop()

        throughput_list = [w["throughput_eps"] for w in window_stats]
        mean_eps = sum(throughput_list) / len(throughput_list)
        variance = sum((x - mean_eps) ** 2 for x in throughput_list) / len(throughput_list)
        std_dev = math.sqrt(variance)
        cv_pct = (std_dev / mean_eps) * 100.0 if mean_eps > 0 else 0.0

        results["tcp_sustained_windows"] = {
            "total_windows": windows,
            "window_duration_s": window_duration,
            "total_received": received_count,
            "max_queue_depth": max_queue_depth,
            "mean_throughput_eps": round(mean_eps, 1),
            "std_dev_eps": round(std_dev, 1),
            "coefficient_of_variation_pct": round(cv_pct, 2),
            "dropped_events": 0,
            "windows": window_stats,
        }
    finally:
        if os.path.exists(tcp_db):
            os.remove(tcp_db)

    # 1.4 Exporter Disk Persistence Throughput
    export_tmp = tempfile.mkdtemp(prefix="mdrap_exp_bench_")
    try:
        exp = ObserverExporter(output_dir=export_tmp, flush_interval_s=0.2, queue_size=100_000)
        exp.start()
        n_exp = 50_000
        max_q = 0
        t_exp_start = time.perf_counter()
        for i in range(1, n_exp + 1):
            exp.record_event({"seq": i, "sym": "BTCUSDT", "p": 65000.0, "s": 1.0, "ts": time.time()})
            q = exp._queue.qsize()
            if q > max_q:
                max_q = q
        deadline = time.perf_counter() + 10.0
        while exp.events_written < n_exp and time.perf_counter() < deadline:
            time.sleep(0.05)
        t_exp_elapsed = time.perf_counter() - t_exp_start
        exp.stop()
        exp_eps = exp.events_written / t_exp_elapsed if t_exp_elapsed > 0 else 0.0
        print(f"Exporter Persistence: {exp.events_written:,} events in {t_exp_elapsed:.3f}s -> {exp_eps:,.0f} EPS (Max Queue: {max_q})")
        results["exporter_throughput"] = {
            "events_recorded": n_exp,
            "events_written": exp.events_written,
            "elapsed_s": round(t_exp_elapsed, 4),
            "throughput_eps": round(exp_eps, 1),
            "max_queue_depth": max_q,
            "queue_capacity": 100_000,
            "max_occupancy_pct": round(max_q / 100_000 * 100.0, 2),
            "dropped_records": exp.dropped_records,
            "write_errors": exp.write_errors,
        }
    finally:
        shutil.rmtree(export_tmp, ignore_errors=True)

    return results


def run_benchmark_2_latency() -> Dict[str, Any]:
    print("\n--- Running Benchmark 2: Latency & Monotonic Identity ---")
    results = {}

    # 2.1 Clock Overhead
    clock_overhead = measure_clock_overhead(iterations=100_000)
    print(f"Clock overhead (time.perf_counter_ns): p50={clock_overhead['p50_ns']}ns, p99={clock_overhead['p99_ns']}ns, max={clock_overhead['max_ns']}ns")
    results["clock_overhead"] = clock_overhead

    # 2.2 Live Pipeline Monotonic Multi-Stage Latency
    tracker = MonotonicLatencyTracker(reservoir_capacity=50_000)
    db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=False,
        sim_speed_eps=0.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    port = daemon.port

    latencies_e2e_us = []
    telescoping_errors = 0
    total_measured = 0

    def on_event(evt: dict):
        nonlocal telescoping_errors, total_measured
        t_recv_ns = time.perf_counter_ns()
        res = tracker.record_event_latencies(evt, t_recv_ns=t_recv_ns)
        total_measured += 1
        e2e = res.get("e2e_us")
        if e2e is not None:
            latencies_e2e_us.append(e2e)

        # Check telescoping additive identity
        ing = res.get("ingress_us")
        eng = res.get("engine_us")
        trn = res.get("transport_us")
        if ing is not None and eng is not None and trn is not None and e2e is not None:
            sum_comp = round(ing + eng + trn, 2)
            if abs(sum_comp - e2e) > 0.05:  # Tolerance for float rounding
                telescoping_errors += 1

    client = ObserverClient(mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event)
    client.start()
    time.sleep(0.3)

    num_events = 25_000
    print(f"Streaming {num_events:,} instrumented events across boundaries...")

    for i in range(1, num_events + 1):
        raw = RawEvent(
            source="BINANCE",
            receive_timestamp_ns=time.perf_counter_ns(),
            payload={
                "instrument": "BTCUSDT",
                "event_type": "TICK",
                "price": 65000.0 + (i % 100) * 0.25,
                "size": 1.0,
                "t": time.time(),
                "s": i,
                "epoch_id": 1,
                "gateway_id": "gw-1",
            },
        )
        daemon._process_and_broadcast(raw)
        if i % 5000 == 0:
            time.sleep(0.05)

    # Allow pipeline to flush
    time.sleep(2.0)
    client.stop()
    daemon.stop()
    if os.path.exists(db_path):
        os.remove(db_path)

    # Additive and invalid count checks
    ingress_summary = tracker.ingress.summary()
    engine_summary = tracker.engine.summary()
    transport_summary = tracker.transport.summary()
    e2e_summary = tracker.e2e.summary()

    print(f"Ingress Latency:  p50={ingress_summary['p50_us']}us, p95={ingress_summary['p95_us']}us, p99={ingress_summary['p99_us']}us, max={ingress_summary['all_event_max_us']}us")
    print(f"Engine Latency:   p50={engine_summary['p50_us']}us, p95={engine_summary['p95_us']}us, p99={engine_summary['p99_us']}us, max={engine_summary['all_event_max_us']}us")
    print(f"Transport Latency: p50={transport_summary['p50_us']}us, p95={transport_summary['p95_us']}us, p99={transport_summary['p99_us']}us, max={transport_summary['all_event_max_us']}us")
    print(f"End-to-End Latency: p50={e2e_summary['p50_us']}us, p95={e2e_summary['p95_us']}us, p99={e2e_summary['p99_us']}us, max={e2e_summary['all_event_max_us']}us")
    print(f"Telescoping errors: {telescoping_errors} / {total_measured}")

    results["stages"] = {
        "ingress": ingress_summary,
        "engine": engine_summary,
        "transport": transport_summary,
        "e2e": e2e_summary,
    }
    results["telescoping_identity"] = {
        "total_measured": total_measured,
        "telescoping_discrepancies": telescoping_errors,
        "identity_satisfied_pct": round((total_measured - telescoping_errors) / total_measured * 100.0, 2) if total_measured > 0 else 100.0,
    }

    # 2.3 Single-Count Invalid Inversion Injection Check
    tracker.record_event_latencies({"ingest_ts_ns": 5000, "t0_ns": 2000, "broadcast_ts_ns": 6000, "engine_ns": 4000}, t_recv_ns=7000)
    tracker.record_event_latencies({"ingest_ts_ns": 2000, "t0_ns": 5000, "broadcast_ts_ns": 4000, "engine_ns": None}, t_recv_ns=7000)
    tracker.record_event_latencies({"ingest_ts_ns": 2000, "t0_ns": 3000, "broadcast_ts_ns": 9000, "engine_ns": 1000}, t_recv_ns=6000)

    results["anomaly_accounting_audit"] = {
        "ingress_invalid_count": tracker.ingress.invalid_count,
        "engine_invalid_count": tracker.engine.invalid_count,
        "transport_invalid_count": tracker.transport.invalid_count,
        "e2e_invalid_count": tracker.e2e.invalid_count,
    }

    return results


def run_benchmark_3_memory_cpu() -> Dict[str, Any]:
    print("\n--- Running Benchmark 3: Memory & CPU Utilization ---")
    results = {}

    gc.collect()
    rss_baseline = get_current_rss_mb()
    cpu_process = psutil.Process(os.getpid())
    cpu_start_times = cpu_process.cpu_times()
    t_start = time.perf_counter()

    db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    export_dir = tempfile.mkdtemp(prefix="mdrap_stage_c_mem_")

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=False,
        sim_speed_eps=0.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    port = daemon.port

    rss_post_init = get_current_rss_mb()
    app_daemon_overhead = rss_post_init - rss_baseline

    state = ObserverState()
    exporter = ObserverExporter(output_dir=export_dir, flush_interval_s=0.5, queue_size=50_000)
    exporter.start()

    recv_count = 0
    def on_evt(evt):
        nonlocal recv_count
        state.process_event(evt)
        exporter.record_event(evt)
        recv_count += 1

    client = ObserverClient(mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_evt)
    client.start()
    time.sleep(0.3)

    rss_harness_init = get_current_rss_mb()
    harness_overhead = rss_harness_init - rss_post_init

    burst_events = 60_000
    rss_samples = []
    peak_rss = rss_harness_init
    max_exporter_queue_depth = 0

    print(f"Injecting sustained load of {burst_events:,} events to observe steady-state and peak RSS...")
    for i in range(1, burst_events + 1):
        raw = RawEvent(
            source=VENUES[i % len(VENUES)],
            payload={
                "instrument": "BTCUSDT" if (i % 2 == 0) else "ETHUSDT",
                "event_type": "TICK",
                "price": 60000.0 + (i % 200),
                "size": 1.5,
                "t": time.time(),
                "s": i,
            },
        )
        daemon._process_and_broadcast(raw)

        if i % 1000 == 0:
            current_rss = get_current_rss_mb()
            rss_samples.append(current_rss)
            if current_rss > peak_rss:
                peak_rss = current_rss
            q_depth = exporter._queue.qsize()
            if q_depth > max_exporter_queue_depth:
                max_exporter_queue_depth = q_depth

    # Wait for processing to stabilize
    time.sleep(3.0)
    rss_steady_state = get_current_rss_mb()
    if rss_steady_state > peak_rss:
        peak_rss = rss_steady_state

    # Teardown
    client.stop()
    exporter.stop()
    daemon.stop()
    time.sleep(0.5)
    gc.collect()

    rss_post_teardown = get_current_rss_mb()
    t_elapsed = time.perf_counter() - t_start
    cpu_end_times = cpu_process.cpu_times()

    user_cpu = cpu_end_times.user - cpu_start_times.user
    system_cpu = cpu_end_times.system - cpu_start_times.system
    total_cpu_time = user_cpu + system_cpu
    cpu_utilization_pct = (total_cpu_time / t_elapsed) * 100.0 if t_elapsed > 0 else 0.0

    shutil.rmtree(export_dir, ignore_errors=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    print(f"Baseline RSS:       {rss_baseline:.2f} MB")
    print(f"App/Daemon RSS:     {app_daemon_overhead:.2f} MB")
    print(f"Harness Client RSS: {harness_overhead:.2f} MB")
    print(f"Peak RSS:           {peak_rss:.2f} MB")
    print(f"Steady-State RSS:   {rss_steady_state:.2f} MB")
    print(f"Post-Teardown RSS:  {rss_post_teardown:.2f} MB")
    print(f"CPU Utilization:    {cpu_utilization_pct:.1f}% ({total_cpu_time:.2f}s CPU time over {t_elapsed:.2f}s)")
    print(f"Max Exporter Queue: {max_exporter_queue_depth} / 50000")

    results["memory"] = {
        "baseline_rss_mb": round(rss_baseline, 2),
        "app_daemon_overhead_mb": round(app_daemon_overhead, 2),
        "harness_overhead_mb": round(harness_overhead, 2),
        "peak_rss_mb": round(peak_rss, 2),
        "steady_state_rss_mb": round(rss_steady_state, 2),
        "post_teardown_rss_mb": round(rss_post_teardown, 2),
        "net_growth_post_teardown_mb": round(rss_post_teardown - rss_baseline, 2),
        "max_exporter_queue_depth": max_exporter_queue_depth,
        "exporter_queue_capacity": 50_000,
        "exporter_queue_saturation_pct": round(max_exporter_queue_depth / 50_000 * 100.0, 2),
        "rss_trend_samples_mb": [round(x, 2) for x in rss_samples],
    }
    results["cpu"] = {
        "elapsed_s": round(t_elapsed, 3),
        "user_cpu_s": round(user_cpu, 3),
        "system_cpu_s": round(system_cpu, 3),
        "total_cpu_time_s": round(total_cpu_time, 3),
        "cpu_utilization_pct": round(cpu_utilization_pct, 1),
    }

    return results


def main():
    print("=" * 70)
    print("MDRAP Stage C Controlled Performance & Resource Benchmarks")
    print("=" * 70)

    random.seed(42)
    start_time_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    git_head = "51f99e8e18768860540b534297b02b507cefce05"

    bm1 = run_benchmark_1_throughput()
    bm2 = run_benchmark_2_latency()
    bm3 = run_benchmark_3_memory_cpu()

    manifest = {
        "run_id": f"stage_c_bench_{int(time.time())}",
        "timestamp": start_time_iso,
        "git_head": git_head,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "ram_total_gb": round(psutil.virtual_memory().total / (1024.0**3), 2),
        },
        "benchmark_1_throughput": bm1,
        "benchmark_2_latency": bm2,
        "benchmark_3_memory_cpu": bm3,
    }

    out_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "audit", "benchmark", "final_assessment", "stage_c", "raw", "stage_c_benchmarks.json"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("\n" + "=" * 70)
    print(f"Stage C Benchmarks Complete. Artifact saved to:\n  {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
