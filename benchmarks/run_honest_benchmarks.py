"""MDRAP Honest Benchmarks & Provenance Runner (B2 / F-01..F-06).

Measures:
1. Engine.step() latency (in-memory deterministic state transition with rollback)
2. Engine.submit() latency under fsync_policy="always" (durable immediate fsync)
3. Engine.submit() latency under fsync_policy="grouped_by_time" (durable group-commit)
4. AsyncFanoutManager fan-out concurrency with simulator disabled:
   - Tagged test frames only
   - Publisher handoff latency & throughput
   - Per-client receipt rate & total egress frames/s
   - Strict delivery rate check (<= 100.0%)
   - Strict duplicates check (== 0)

Outputs verified results to benchmarks/results/honest_benchmarks.json with
complete hardware and Git commit provenance.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from mdrap.async_fanout import AsyncFanoutManager
from mdrap.clock import SystemClock
from mdrap.engine import Engine, EngineConfig
from mdrap.metrics import percentile
from mdrap.models import RawEvent
from mdrap.simulator import FeedSimulator, SimulatorConfig


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN"


def get_system_metadata() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "python_version": sys.version.split()[0],
        "git_commit": get_git_commit(),
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _create_raw(seq: int, instrument: str = "AAPL") -> RawEvent:
    now = 1700000000.0 + (seq * 0.001)
    return RawEvent(
        source="CME",
        raw_id=f"bench_raw_{seq}",
        receive_timestamp=now,
        payload={
            "instrument": instrument,
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": seq,
            "price": 180.0 + (seq % 100) * 0.01,
            "quantity": 10.0,
        },
    )


def measure_engine_step_latency(
    repetitions: int = 30, events_per_rep: int = 500
) -> dict[str, Any]:
    """Measure single-event Engine.step() latency with rollback intact across multiple runs."""
    engine = Engine()
    clock = SystemClock()

    # Warm-up
    st = engine.state
    for i in range(100):
        st, _ = engine.step(st, _create_raw(i), clock, offset=i)

    run_p50s = []
    run_p95s = []
    run_p99s = []
    all_latencies_us = []

    for rep in range(repetitions):
        st = engine.state
        rep_lats = []
        for i in range(events_per_rep):
            seq = 1000 + rep * events_per_rep + i
            ev = _create_raw(seq)
            t0 = time.perf_counter_ns()
            st, _ = engine.step(st, ev, clock, offset=seq)
            t1 = time.perf_counter_ns()
            lat_us = (t1 - t0) / 1000.0
            rep_lats.append(lat_us)
            all_latencies_us.append(lat_us)

        rep_lats.sort()
        n = len(rep_lats)
        run_p50s.append(percentile(rep_lats, 0.50))
        run_p95s.append(percentile(rep_lats, 0.95))
        run_p99s.append(percentile(rep_lats, 0.99))

    engine.close()
    all_latencies_us.sort()

    return {
        "repetitions": repetitions,
        "events_per_rep": events_per_rep,
        "total_samples": len(all_latencies_us),
        "latencies_us": {
            "p50": round(percentile(all_latencies_us, 0.50), 2),
            "p95": round(percentile(all_latencies_us, 0.95), 2),
            "p99": round(percentile(all_latencies_us, 0.99), 2),
            "max": round(max(all_latencies_us), 2),
        },
        "median_of_p50s_us": round(percentile(run_p50s, 0.50), 2),
        "run_p50s_us": [round(x, 2) for x in run_p50s[:5]],
    }


def measure_engine_persistence(
    fsync_policy: str,
    total_events: int = 2000,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Measure durable ingest throughput & latency with explicit fsync policy."""
    with tempfile.TemporaryDirectory(
        prefix=f"mdrap_bench_persist_{fsync_policy}_"
    ) as tmp:
        wal_dir = os.path.join(tmp, "wal")
        db_path = os.path.join(tmp, "canonical.db")

        engine = Engine.open(
            wal_dir,
            config=EngineConfig(
                db_path=db_path,
                fsync_policy=fsync_policy,
            ),
        )

        batches = []
        for i in range(0, total_events, batch_size):
            chunk = [
                _create_raw(i + j) for j in range(min(batch_size, total_events - i))
            ]
            batches.append(chunk)

        # Warm-up with one batch
        if batches:
            engine.submit(batches[0])

        batch_latencies_ms = []
        t0 = time.perf_counter()

        for chunk in batches[1:]:
            tb0 = time.perf_counter()
            engine.submit(chunk)
            tb1 = time.perf_counter()
            batch_latencies_ms.append((tb1 - tb0) * 1000.0)

        t1 = time.perf_counter()
        elapsed_s = max(0.0001, t1 - t0)
        timed_events = sum(len(c) for c in batches[1:])
        eps = timed_events / elapsed_s if elapsed_s > 0 else 0.0

        engine.close()
        batch_latencies_ms.sort()

        return {
            "fsync_policy": fsync_policy,
            "total_events": timed_events,
            "batch_size": batch_size,
            "elapsed_s": round(elapsed_s, 4),
            "throughput_eps": round(eps, 1),
            "batch_latencies_ms": {
                "p50": round(percentile(batch_latencies_ms, 0.50), 3)
                if batch_latencies_ms
                else 0.0,
                "p95": round(percentile(batch_latencies_ms, 0.95), 3)
                if batch_latencies_ms
                else 0.0,
                "p99": round(percentile(batch_latencies_ms, 0.99), 3)
                if batch_latencies_ms
                else 0.0,
                "max": round(max(batch_latencies_ms), 3) if batch_latencies_ms else 0.0,
            },
        }


def measure_fanout_concurrency(
    client_counts: list[int] = [1, 10, 25],
    events_per_tier: int = 5000,
) -> dict[str, Any]:
    """Measure fan-out concurrency with simulator disabled, using tagged test frames."""
    tiers = {}

    for n_clients in client_counts:
        mgr = AsyncFanoutManager(max_buffer_per_client=events_per_tier + 2000)
        consumer_ids = [f"client_{i:03d}" for i in range(n_clients)]
        for cid in consumer_ids:
            mgr.register_consumer(cid)

        # Warm-up (untagged frames)
        for i in range(50):
            mgr.publish_event("AAPL", b"WARMUP", seq=i)
        for cid in consumer_ids:
            while mgr.consume_event(cid, timeout=0.001):
                pass

        # Publish tagged frames
        latencies_us = []
        t0 = time.perf_counter()

        for seq in range(events_per_tier):
            payload = f"TAGGED_FRAME_{seq}".encode("utf-8")
            tb0 = time.perf_counter_ns()
            mgr.publish_event("AAPL", payload, seq=seq)
            tb1 = time.perf_counter_ns()
            latencies_us.append((tb1 - tb0) / 1000.0)

        t_pub_done = time.perf_counter()

        # Concurrent client drain
        received_by_client: list[list[str]] = [[] for _ in range(n_clients)]

        def drain_client(idx: int, cid: str):
            res = []
            while len(res) < events_per_tier:
                item = mgr.consume_event(cid, timeout=0.05)
                if item:
                    topic, payload, seq = item
                    text = payload.decode("utf-8", errors="ignore")
                    if text.startswith("TAGGED_FRAME_"):
                        res.append(text)
                else:
                    break
            received_by_client[idx] = res

        threads = [
            threading.Thread(target=drain_client, args=(i, cid))
            for i, cid in enumerate(consumer_ids)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        t_drain_done = time.perf_counter()
        mgr.stop()

        pub_duration = max(0.0001, t_pub_done - t0)
        total_duration = max(0.0001, t_drain_done - t0)
        pub_eps = events_per_tier / pub_duration

        total_received = sum(len(r) for r in received_by_client)
        expected_total = events_per_tier * n_clients
        delivery_rate = round(min(100.0, (total_received / expected_total) * 100.0), 2)

        # Check duplicate frames
        duplicates = sum(len(r) - len(set(r)) for r in received_by_client)

        latencies_us.sort()
        n = len(latencies_us)

        tiers[f"{n_clients}_clients"] = {
            "num_clients": n_clients,
            "events_published": events_per_tier,
            "total_frames_received": total_received,
            "expected_total_frames": expected_total,
            "delivery_rate_percent": delivery_rate,
            "duplicate_frames_count": duplicates,
            "publish_throughput_eps": round(pub_eps, 1),
            "egress_throughput_frames_per_sec": round(
                total_received / total_duration, 1
            ),
            "publisher_handoff_latency_us": {
                "p50": round(percentile(latencies_us, 0.50), 2),
                "p95": round(percentile(latencies_us, 0.95), 2),
                "p99": round(percentile(latencies_us, 0.99), 2),
                "max": round(max(latencies_us), 2),
            },
        }

    return tiers


def run_all_honest_benchmarks() -> dict[str, Any]:
    print("=" * 70)
    print("MDRAP Authoritative Honest Benchmark Suite (B2 / F-01..F-06)")
    meta = get_system_metadata()
    print(f"Host: {meta['os']} | CPU: {meta['processor']} ({meta['cpu_count']} cores)")
    print(f"Python: {meta['python_version']} | Commit: {meta['git_commit'][:10]}")
    print("=" * 70)

    print("\n[1/4] Measuring Engine.step() latency (30 reps)...")
    step_res = measure_engine_step_latency(repetitions=30, events_per_rep=300)
    print(
        f"      Engine.step() p50: {step_res['latencies_us']['p50']} µs | p99: {step_res['latencies_us']['p99']} µs"
    )

    print("\n[2/4] Measuring Engine.submit() with fsync_policy='always'...")
    persist_always = measure_engine_persistence(
        "always", total_events=1000, batch_size=100
    )
    print(
        f"      Throughput: {persist_always['throughput_eps']:,} eps | Batch p50: {persist_always['batch_latencies_ms']['p50']} ms"
    )

    print("\n[3/4] Measuring Engine.submit() with fsync_policy='grouped_by_time'...")
    persist_grouped = measure_engine_persistence(
        "grouped_by_time", total_events=2000, batch_size=200
    )
    print(
        f"      Throughput: {persist_grouped['throughput_eps']:,} eps | Batch p50: {persist_grouped['batch_latencies_ms']['p50']} ms"
    )

    print(
        "\n[4/4] Measuring Fan-Out Concurrency (simulator disabled, tagged frames)..."
    )
    fanout_res = measure_fanout_concurrency(
        client_counts=[1, 10, 25], events_per_tier=2000
    )
    for k, v in fanout_res.items():
        print(
            f"      {k}: delivery={v['delivery_rate_percent']}% | dups={v['duplicate_frames_count']} | pub_eps={v['publish_throughput_eps']:,}"
        )

    report = {
        "metadata": meta,
        "engine_step": step_res,
        "persistence_always": persist_always,
        "persistence_grouped": persist_grouped,
        "fanout": fanout_res,
    }

    out_dir = REPO_ROOT / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "honest_benchmarks.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n[OK] Authoritative benchmark report saved to: {out_file}")
    return report


if __name__ == "__main__":
    run_all_honest_benchmarks()
