"""MDRAP End-to-End Performance and Durability Benchmark Harness (Spec §8, §20 Phase 5).

Measures real institutional pipeline throughput, tail latencies, memory footprint,
and journal recovery time using reproducible seeds.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

# Ensure src/ is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mdrap.metrics import get_rss_mb, percentile
from mdrap.models import EventType, RawEvent
from mdrap.pipeline import Pipeline
from mdrap.simulator import FeedSimulator, SimulatorConfig
from mdrap.storage import Store


def benchmark_pipeline_throughput(
    num_events: int = 50_000,
    batch_size: int = 1024,
    use_batch_mode: bool = False,
    seed: int = 42,
) -> dict:
    """Measure pipeline throughput, p50/p95/p99/p99.9/max latencies, and RSS on disk."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_bench_")
    db_path = os.path.join(temp_dir, "bench.db")
    gc.collect()
    rss_before = get_rss_mb()

    store = Store(db_path)
    pipeline = Pipeline(store=store)
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))

    t0 = time.perf_counter()
    if use_batch_mode:
        batch: list[RawEvent] = []
        for raw, _ in sim.generate():
            batch.append(raw)
            if len(batch) >= batch_size:
                pipeline.process_batch(batch)
                batch.clear()
        if batch:
            pipeline.process_batch(batch)
    else:
        for raw, _ in sim.generate():
            pipeline.process_one(raw)

    pipeline.finish()
    t1 = time.perf_counter()
    elapsed_s = max(0.0001, t1 - t0)
    eps = num_events / elapsed_s
    rss_after = get_rss_mb()

    summary = pipeline.metrics.summary()
    e2e = summary.get("e2e_latency_us", {})
    proc = summary.get("processing_latency_us", {})

    result = {
        "num_events": num_events,
        "mode": "batch" if use_batch_mode else "single",
        "batch_size": batch_size if use_batch_mode else 1,
        "elapsed_s": round(elapsed_s, 4),
        "events_per_second": round(eps, 1),
        "rss_mb_before": round(rss_before, 2),
        "rss_mb_after": round(rss_after, 2),
        "rss_mb_growth": round(max(0.0, rss_after - rss_before), 2),
        "e2e_latency_us": e2e,
        "processing_latency_us": proc,
        "quality_counts": summary.get("quality_counts", {}),
        "dropped_events": summary.get("dropped", 0),
        "hook_errors": summary.get("hook_errors", 0),
        "writer_failures": summary.get("writer_failures", 0),
    }

    try:
        store.close()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return result


def benchmark_crash_recovery(num_events: int = 20_000, seed: int = 42) -> dict:
    """Measure replay/recovery time from unprojected journal file."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_bench_recovery_")
    db_path = os.path.join(temp_dir, "bench_recovery.db")
    journal_path = f"{db_path}.journal"

    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))

    # Pre-populate journal directly simulating unprojected crash state
    lines_written = 0
    t_gen_0 = time.perf_counter()
    with open(journal_path, "w", encoding="utf-8") as f:
        for raw, _ in sim.generate():
            row = {
                "event_id": str(raw.raw_id),
                "instrument_id": raw.payload.get("instrument", "TEST"),
                "event_type": "TRADE",
                "exchange_timestamp": raw.receive_timestamp or time.time(),
                "receive_timestamp": raw.receive_timestamp or time.time(),
                "processing_timestamp": time.time(),
                "source": raw.source,
                "sequence_number": lines_written,
                "price": float(raw.payload.get("price", 100.0)),
                "quantity": float(raw.payload.get("quantity", 10.0)),
                "bid_price": None,
                "bid_size": None,
                "ask_price": None,
                "ask_size": None,
                "quality_status": "VALID",
                "reasons": [],
                "raw_id": lines_written,

            }
            f.write(json.dumps({"type": "canonical", "payload": row}) + "\n")
            lines_written += 1
        f.flush()
        os.fsync(f.fileno())


    journal_size_mb = os.path.getsize(journal_path) / (1024 * 1024)

    # Time opening the Store, which recovers the journal
    gc.collect()
    t_rec_0 = time.perf_counter()
    store = Store(db_path)
    t_rec_1 = time.perf_counter()
    recovery_s = max(0.0001, t_rec_1 - t_rec_0)
    recovered_events = store.conn.execute("SELECT count(*) FROM canonical_events").fetchone()[0]
    store.close()


    result = {
        "journal_events": lines_written,
        "recovered_events": recovered_events,
        "journal_size_mb": round(journal_size_mb, 2),
        "recovery_time_s": round(recovery_s, 4),
        "recovery_rate_eps": round(recovered_events / recovery_s, 1),
    }

    shutil.rmtree(temp_dir, ignore_errors=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="MDRAP Benchmark Suite")
    parser.add_argument("--events", type=int, default=30_000, help="Number of events to simulate")
    parser.add_argument("--batch-size", type=int, default=1024, help="Batch size for batch mode")
    parser.add_argument("--output", type=str, default="", help="Path to save JSON benchmark output")
    args = parser.parse_args()

    print("=" * 60)
    print("MDRAP Performance & Durability Benchmark Harness")
    print("=" * 60)

    print(f"\n1. Measuring Per-Event Mode ({args.events} events)...")
    res_single = benchmark_pipeline_throughput(num_events=args.events, use_batch_mode=False)
    print(f"   Throughput: {res_single['events_per_second']:,} ev/s")
    print(f"   p50 Latency: {res_single['processing_latency_us'].get('p50')} us | p99: {res_single['processing_latency_us'].get('p99')} us")
    print(f"   RSS Growth: {res_single['rss_mb_growth']} MB")

    print(f"\n2. Measuring Batch Mode ({args.events} events, batch_size={args.batch_size})...")
    res_batch = benchmark_pipeline_throughput(num_events=args.events, batch_size=args.batch_size, use_batch_mode=True)
    print(f"   Throughput: {res_batch['events_per_second']:,} ev/s")
    print(f"   p50 Latency: {res_batch['processing_latency_us'].get('p50')} us | p99: {res_batch['processing_latency_us'].get('p99')} us")
    print(f"   RSS Growth: {res_batch['rss_mb_growth']} MB")

    print("\n3. Measuring Crash Recovery Performance...")
    res_recovery = benchmark_crash_recovery(num_events=min(args.events, 20_000))
    print(f"   Replay: {res_recovery['recovered_events']:,} events ({res_recovery['journal_size_mb']} MB) in {res_recovery['recovery_time_s']}s")
    print(f"   Recovery Rate: {res_recovery['recovery_rate_eps']:,} ev/s")

    report = {
        "timestamp": time.time(),
        "single_event": res_single,
        "batch": res_batch,
        "recovery": res_recovery,
    }

    out_path = args.output
    if not out_path:
        out_dir = ROOT / "benchmarks" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / "latest_benchmarks.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n[OK] Results saved to {out_path}")


if __name__ == "__main__":
    main()
