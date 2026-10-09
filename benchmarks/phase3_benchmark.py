"""MDRAP Phase 3 Empirical Benchmark Suite.

Measures:
  1. Ingress Replay Adapter throughput and latency percentiles.
  2. Durable Usage Metering write throughput and latency percentiles (SQLite WAL).
  3. High Availability Failover heartbeat dispatch and evaluation latency.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdrap.failover import FailoverNode, NodeState
from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.metering import DurableUsageMeter, MeteringUnit


def _compute_percentiles(latencies_ns: list[int]) -> dict[str, float]:
    if not latencies_ns:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    latencies_ns.sort()
    n = len(latencies_ns)
    return {
        "p50_us": round(latencies_ns[int(n * 0.50)] / 1000.0, 2),
        "p95_us": round(latencies_ns[int(n * 0.95)] / 1000.0, 2),
        "p99_us": round(latencies_ns[int(n * 0.99)] / 1000.0, 2),
        "max_us": round(latencies_ns[-1] / 1000.0, 2),
    }


def benchmark_ingress_adapter(event_count: int = 50000) -> dict[str, Any]:
    print(f"[*] Running Ingress Adapter Benchmark ({event_count:,} events)...")
    frames = [
        {"seq": i, "sym": "AAPL", "px": 150.0 + (i % 100) * 0.01, "sz": 10.0, "type": "TRADE"}
        for i in range(1, event_count + 1)
    ]
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="BENCH_NASDAQ", feed_id="MOLD64"),
        frames=frames,
    )
    adapter.connect()

    latencies_ns: list[int] = []
    t0 = time.perf_counter()

    for _ in range(event_count):
        s0 = time.perf_counter_ns()
        raw = adapter.poll()
        if raw is not None:
            _ = adapter.normalize(raw)
        s1 = time.perf_counter_ns()
        latencies_ns.append(s1 - s0)

    total_time_s = time.perf_counter() - t0
    rate = round(event_count / total_time_s, 1)
    pct = _compute_percentiles(latencies_ns)

    return {
        "event_count": event_count,
        "elapsed_s": round(total_time_s, 4),
        "rate_eps": rate,
        **pct,
    }


def benchmark_durable_metering(batch_count: int = 20000) -> dict[str, Any]:
    print(f"[*] Running Durable Metering Benchmark ({batch_count:,} records)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "bench_metering.db")
        meter = DurableUsageMeter(db_path)

        latencies_ns: list[int] = []
        t0 = time.perf_counter()

        for i in range(batch_count):
            s0 = time.perf_counter_ns()
            meter.record_usage(
                client_id="Firm_Algo",
                tenant_id="Tenant_1",
                source="NASDAQ",
                symbol="AAPL",
                count=100,
                idempotency_key=f"bench_key_{i}",
                unit=MeteringUnit.DISTRIBUTED_EVENT,
            )
            s1 = time.perf_counter_ns()
            latencies_ns.append(s1 - s0)

        total_time_s = time.perf_counter() - t0
        rate = round(batch_count / total_time_s, 1)
        pct = _compute_percentiles(latencies_ns)
        meter.close()

        return {
            "batch_count": batch_count,
            "elapsed_s": round(total_time_s, 4),
            "rate_ops": rate,
            **pct,
        }


def benchmark_failover_heartbeats(hb_count: int = 50000) -> dict[str, Any]:
    print(f"[*] Running High Availability Heartbeat Benchmark ({hb_count:,} heartbeats)...")
    primary = FailoverNode("primary-1", initial_state=NodeState.PRIMARY, initial_epoch=1)
    standby = FailoverNode("standby-1", initial_state=NodeState.STANDBY, initial_epoch=1)

    latencies_ns: list[int] = []
    t0 = time.perf_counter()

    for i in range(hb_count):
        s0 = time.perf_counter_ns()
        hb = primary.send_heartbeat(last_committed_seq=i)
        standby.receive_heartbeat(hb)
        s1 = time.perf_counter_ns()
        latencies_ns.append(s1 - s0)

    total_time_s = time.perf_counter() - t0
    rate = round(hb_count / total_time_s, 1)
    pct = _compute_percentiles(latencies_ns)

    return {
        "heartbeat_count": hb_count,
        "elapsed_s": round(total_time_s, 4),
        "rate_ops": rate,
        **pct,
    }


def run_all_benchmarks() -> dict[str, Any]:
    results = {
        "timestamp": time.time(),
        "ingress_adapter": benchmark_ingress_adapter(50000),
        "durable_metering": benchmark_durable_metering(20000),
        "failover_heartbeats": benchmark_failover_heartbeats(50000),
    }

    out_path = os.path.join("audit", "phase3", "benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n[+] Benchmark completed. Raw results written to {out_path}")
    return results


if __name__ == "__main__":
    run_all_benchmarks()
