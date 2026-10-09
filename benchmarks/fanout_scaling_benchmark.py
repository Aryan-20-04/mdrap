"""
MDRAP Phase 8 Asynchronous Fan-Out Concurrency Benchmark.
Measures publisher handoff throughput, delivery rate, and latency percentiles
across 1, 25, 50, and 100+ concurrent stream consumers.
"""

import sys
import os
import time
import json
import statistics
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.async_fanout import AsyncFanoutManager


def run_fanout_benchmark(client_counts=[1, 25, 50, 100], events_per_tier=10000):
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "events_per_tier": events_per_tier,
        "tiers": {},
    }

    print(f"=== MDRAP Phase 8 Fan-Out Concurrency Benchmark ({events_per_tier} events/tier) ===")

    for n_clients in client_counts:
        mgr = AsyncFanoutManager(max_buffer_per_client=events_per_tier + 1000)
        consumer_ids = [f"client_{i:03d}" for i in range(n_clients)]
        for cid in consumer_ids:
            mgr.register_consumer(cid)

        # Warm up
        for i in range(100):
            mgr.publish_event("AAPL", b"WARMUP", seq=i)
        time.sleep(0.02)
        # Drain warmup
        for cid in consumer_ids:
            while mgr.consume_event(cid, timeout=0.001):
                pass

        # Measurement run
        latencies_us = []
        t_start = time.perf_counter()

        for seq in range(events_per_tier):
            t0 = time.perf_counter_ns()
            mgr.publish_event("AAPL", b"TICK_DATA_SAMPLE_PAYLOAD_32B", seq=seq)
            t1 = time.perf_counter_ns()
            latencies_us.append((t1 - t0) / 1000.0)

        t_publish_done = time.perf_counter()

        # Drain all clients
        drain_done = threading.Event()
        consumed_per_client = [0] * n_clients

        def client_drainer(idx, cid):
            c = 0
            while c < events_per_tier:
                item = mgr.consume_event(cid, timeout=0.1)
                if item:
                    c += 1
                else:
                    break
            consumed_per_client[idx] = c

        threads = [
            threading.Thread(target=client_drainer, args=(i, cid))
            for i, cid in enumerate(consumer_ids)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        t_drain_done = time.perf_counter()
        mgr.stop()

        publish_duration = t_publish_done - t_start
        total_duration = t_drain_done - t_start
        publish_eps = events_per_tier / publish_duration if publish_duration > 0 else 0
        total_delivered = sum(consumed_per_client)
        egress_eps = total_delivered / total_duration if total_duration > 0 else 0

        latencies_us.sort()
        n = len(latencies_us)
        p50 = latencies_us[int(n * 0.50)]
        p95 = latencies_us[int(n * 0.95)]
        p99 = latencies_us[int(n * 0.99)]
        max_lat = latencies_us[-1]

        tier_res = {
            "num_clients": n_clients,
            "publish_throughput_eps": round(publish_eps, 1),
            "egress_throughput_frames_per_sec": round(egress_eps, 1),
            "publisher_handoff_latency_us": {
                "p50": round(p50, 3),
                "p95": round(p95, 3),
                "p99": round(p99, 3),
                "max": round(max_lat, 3),
            },
            "events_published": events_per_tier,
            "total_frames_delivered": total_delivered,
            "delivery_rate_percent": round((total_delivered / (events_per_tier * n_clients)) * 100.0, 2),
        }
        results["tiers"][f"{n_clients}_clients"] = tier_res

        print(
            f"[{n_clients:3d} Clients] Publish EPS: {publish_eps:10.1f} | "
            f"Egress Frames/s: {egress_eps:10.1f} | "
            f"Handoff p50: {p50:5.2f}s, p99: {p99:5.2f}s | "
            f"Delivered: {total_delivered}/{events_per_tier * n_clients} ({tier_res['delivery_rate_percent']}%)"
        )

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "audit",
        "phase8",
        "fanout_benchmark_results.json",
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n[OK] Saved fanout benchmark results to: {out_path}")
    return results


if __name__ == "__main__":
    run_fanout_benchmark()
