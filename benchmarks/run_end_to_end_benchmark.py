"""
MDRAP Full-System Benchmark Suite - Phase 5: End-to-End Pipeline Benchmark
Measures live Producer -> MarketDataDaemon -> ObserverClient -> ObserverState -> ObserverExporter.
Collects throughput, end-to-end delivery latency, queue depths, memory progression, and JSONL integrity.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List

# Ensure src and observer in sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "external_sidecar", "mdrap_observer"
        )
    ),
)

from mdrap.models import RawEvent
from mdrap.service import MarketDataDaemon
from observer.client import ObserverClient
from observer.exporter import ObserverExporter
from observer.state import ObserverState, VENUES


def get_rss_mb() -> float:
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def run_e2e_benchmark(
    duration_s: float = 300.0, target_eps: float = 1000.0, mode: str = "tcp"
) -> Dict[str, Any]:
    print(
        f"\n[E2E] Initializing End-to-End Benchmark: duration={duration_s}s, target_rate={target_eps} EPS, transport={mode.upper()}..."
    )

    os.environ["MDRAP_API_KEY_SALT"] = "bench_salt_e2e_12345"
    os.environ["MDRAP_DEMO"] = "1"

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    export_dir = tempfile.mkdtemp(prefix="mdrap_e2e_export_")

    ring_name = f"mdrap_e2e_ring_{int(time.time() * 1000) % 1000000}"

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=(mode == "shm"),
        shm_name=ring_name,
        sim_speed_eps=0.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    port = daemon.port

    state = ObserverState()
    exporter = ObserverExporter(
        output_dir=export_dir, flush_interval_s=0.25, queue_size=50000
    )
    exporter.start()

    latencies_us: List[float] = []
    received_count = 0

    def on_event(evt: dict):
        nonlocal received_count
        t_recv = time.time()
        state.process_event(evt)
        exporter.record_event(evt)
        received_count += 1
        t_pub = evt.get("ts") or evt.get("broadcast_ts") or 0.0
        if t_pub > 0:
            lat = (t_recv - t_pub) * 1e6
            if lat >= 0:
                latencies_us.append(lat)

    if mode == "shm":
        client = ObserverClient(
            mode="shm", ring_name=ring_name, on_event_callback=on_event
        )
    else:
        client = ObserverClient(
            mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event
        )
    client.start()
    time.sleep(0.3)

    rss_samples = [get_rss_mb()]
    timestamps = [0.0]
    producer_sent = 0
    t_start = time.perf_counter()
    interval = 1.0 / target_eps
    next_tick = t_start

    symbols = ["BTCUSDT", "BTC-USD", "ETHUSDT", "ETH-USD"]
    venues = VENUES

    print(f"[E2E] Starting live production injection for {duration_s:.1f} seconds...")
    last_sample_t = t_start

    batch_size = 10
    batch_interval = batch_size / target_eps
    next_batch_time = time.perf_counter()

    try:
        while True:
            t_now = time.perf_counter()
            elapsed = t_now - t_start
            if elapsed >= duration_s:
                break

            for _ in range(batch_size):
                producer_sent += 1
                venue = venues[producer_sent % len(venues)]
                symbol = symbols[producer_sent % len(symbols)]
                base_p = 65000.0 if "BTC" in symbol else 3400.0
                price = base_p + (producer_sent % 100) * 0.1
                t_pub = time.perf_counter()

                raw = RawEvent(
                    source=venue,
                    receive_timestamp=t_pub,
                    payload={
                        "instrument": symbol,
                        "event_type": "TICK",
                        "price": price,
                        "bid": price - 0.5,
                        "ask": price + 0.5,
                        "bid_size": 10.0,
                        "ask_size": 10.0,
                        "quantity": 1.0,
                        "sequence": producer_sent,
                        "exchange_ts": time.time(),
                    },
                )
                daemon._process_and_broadcast(raw)

            # Sample every 5 seconds
            if t_now - last_sample_t >= 5.0:
                rss_samples.append(get_rss_mb())
                timestamps.append(round(elapsed, 1))
                last_sample_t = t_now
                print(
                    f"  [T+{int(elapsed)}s] Sent: {producer_sent:,} | Recv: {received_count:,} | RSS: {rss_samples[-1]:.1f} MB | Queue: {exporter._queue.qsize()}"
                )

            next_batch_time += batch_interval
            sleep_s = next_batch_time - time.perf_counter()
            if sleep_s > 0.001:
                time.sleep(sleep_s)
            elif sleep_s < -0.1:
                next_batch_time = time.perf_counter()

    finally:
        print("[E2E] Injection complete. Allowing queues and socket to drain...")
        time.sleep(1.0)
        client.stop()
        daemon.stop()
        exporter.stop()

    t_total = time.perf_counter() - t_start
    rss_end = get_rss_mb()
    rss_samples.append(rss_end)
    timestamps.append(round(t_total, 1))

    # Verify JSONL integrity
    jsonl_valid = True
    jsonl_line_count = 0
    events_file = exporter.events_file
    if os.path.exists(events_file):
        with open(events_file, "r", encoding="utf-8") as fp:
            for line in fp:
                line_str = line.strip()
                if line_str:
                    try:
                        json.loads(line_str)
                        jsonl_line_count += 1
                    except Exception:
                        jsonl_valid = False
                        break

    file_size_bytes = os.path.getsize(events_file) if os.path.exists(events_file) else 0
    shutil.rmtree(export_dir, ignore_errors=True)
    try:
        os.remove(db_path)
    except Exception:
        pass

    latencies_us.sort()
    n = len(latencies_us) or 1

    return {
        "duration_target_s": duration_s,
        "duration_actual_s": round(t_total, 3),
        "target_eps": target_eps,
        "transport": mode.upper(),
        "producer_events_sent": producer_sent,
        "observer_events_received": received_count,
        "events_written_to_jsonl": exporter.events_written,
        "dropped_records": exporter.dropped_records,
        "write_errors": exporter.write_errors,
        "delivery_rate_pct": round((received_count / max(1, producer_sent)) * 100.0, 4),
        "effective_throughput_eps": round(received_count / max(0.001, t_total), 1),
        "latency_us": {
            "p50": round(latencies_us[int(n * 0.50)], 2) if n > 1 else 0.0,
            "p90": round(latencies_us[int(n * 0.90)], 2) if n > 1 else 0.0,
            "p95": round(latencies_us[int(n * 0.95)], 2) if n > 1 else 0.0,
            "p99": round(latencies_us[int(n * 0.99)], 2) if n > 1 else 0.0,
            "p99_9": round(latencies_us[int(n * 0.999)], 2) if n > 1 else 0.0,
            "max": round(latencies_us[-1], 2) if latencies_us else 0.0,
        },
        "rss_progression_mb": {
            "initial": round(rss_samples[0], 2),
            "peak": round(max(rss_samples), 2),
            "final": round(rss_end, 2),
            "net_growth": round(rss_end - rss_samples[0], 2),
            "samples": [
                {"t_s": t, "rss_mb": round(m, 2)}
                for t, m in zip(timestamps, rss_samples)
            ],
        },
        "jsonl_audit": {
            "is_valid_json": jsonl_valid,
            "lines_verified": jsonl_line_count,
            "file_size_bytes": file_size_bytes,
        },
        "sequence_domains": {
            "stream_gaps": state.stream_health.gaps,
            "stream_resets": state.stream_health.resets,
            "feed_gaps": sum(v.feed_gaps for v in state.health.values()),
            "feed_duplicates": sum(v.feed_duplicates for v in state.health.values()),
            "feed_out_of_order": sum(
                v.feed_out_of_order for v in state.health.values()
            ),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="MDRAP End-to-End Pipeline Benchmark")
    parser.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="Test duration in seconds (default: 300.0)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1000.0,
        help="Target ingestion rate in EPS (default: 1000.0)",
    )
    parser.add_argument(
        "--mode", type=str, default="tcp", choices=["tcp", "shm"], help="Transport mode"
    )
    parser.add_argument(
        "--out",
        type=str,
        default="audit/benchmark/raw/end_to_end_benchmark.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    print("=" * 80)
    print(" MDRAP v6.0.8 End-to-End System Benchmark")
    print("=" * 80)

    result = run_e2e_benchmark(
        duration_s=args.duration, target_eps=args.rate, mode=args.mode
    )

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fp:
        json.dump(result, fp, indent=2)

    print("\n" + "=" * 80)
    print(" End-to-End Benchmark Summary")
    print("=" * 80)
    print(f" Duration:            {result['duration_actual_s']:.1f} s")
    print(f" Events Sent:        {result['producer_events_sent']:,}")
    print(f" Events Received:    {result['observer_events_received']:,}")
    print(f" Delivery Rate:      {result['delivery_rate_pct']:.2f}%")
    print(f" Throughput:         {result['effective_throughput_eps']:,} EPS")
    print(f" Latency p50:        {result['latency_us']['p50']:.1f} us")
    print(f" Latency p95:        {result['latency_us']['p95']:.1f} us")
    print(f" Latency p99:        {result['latency_us']['p99']:.1f} us")
    print(f" Latency max:        {result['latency_us']['max']:.1f} us")
    print(
        f" RSS Net Growth:     {result['rss_progression_mb']['net_growth']:.2f} MB (Peak: {result['rss_progression_mb']['peak']:.2f} MB)"
    )
    print(
        f" JSONL Valid:        {result['jsonl_audit']['is_valid_json']} ({result['jsonl_audit']['lines_verified']:,} lines)"
    )
    print(f" Stream Gaps:        {result['sequence_domains']['stream_gaps']}")
    print(f" Feed Gaps:          {result['sequence_domains']['feed_gaps']}")
    print(f" Results Saved:      {args.out}")
    print("=" * 80)


if __name__ == "__main__":
    main()
