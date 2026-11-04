"""Benchmark measuring the complete end-to-end production path (Ingest -> Normalize -> Quality -> WAL -> Projections)."""

import os
import shutil
import tempfile
import time

from mdrap import Engine, EngineConfig, RawEvent
from mdrap.projection import SQLiteProjection


def _make_event(i: int) -> RawEvent:
    return RawEvent(
        source="BENCH_SRC",
        payload={
            "instrument": "AAPL",
            "event_type": "QUOTE",
            "bid": 100.0 + (i % 10) * 0.05,
            "ask": 100.05 + (i % 10) * 0.05,
            "exchange_ts": 1700000000.0 + i * 0.001,
            "sequence": i + 1,
        },
        receive_timestamp=1700000000.0 + i * 0.001,
        raw_id=f"raw_{i}",
    )


def benchmark_full_pipeline(num_events: int = 10_000, fsync_policy: str = "never"):
    temp_dir = tempfile.mkdtemp(prefix="mdrap_bench_full_")
    db_path = os.path.join(temp_dir, "projection.db")
    try:
        cfg = EngineConfig(
            wal_dir=temp_dir,
            fsync_policy=fsync_policy,
        )
        engine = Engine(cfg)
        proj = SQLiteProjection(db_path=db_path, name="bench_proj")
        engine.subscribe(proj)

        events = [_make_event(i) for i in range(num_events)]

        # Warmup 500 events
        for ev in events[:500]:
            engine.submit(ev)

        latencies_us = []
        t_start = time.perf_counter()
        for ev in events[500:]:
            t0 = time.perf_counter_ns()
            engine.submit(ev)
            t1 = time.perf_counter_ns()
            latencies_us.append((t1 - t0) / 1000.0)
        elapsed = time.perf_counter() - t_start

        measured_events = num_events - 500
        throughput = measured_events / elapsed
        latencies_us.sort()
        n = len(latencies_us)

        p50 = latencies_us[int(n * 0.50)]
        p95 = latencies_us[int(n * 0.95)]
        p99 = latencies_us[int(n * 0.99)]
        p99_9 = latencies_us[int(n * 0.999)]

        print(f"===========================================================")
        print(f"FULL PRODUCTION PIPELINE BENCHMARK (fsync_policy='{fsync_policy}')")
        print(f"===========================================================")
        print(f"Events: {measured_events}")
        print(f"Elapsed: {elapsed:.3f} s")
        print(f"Throughput: {throughput:.1f} EPS")
        print(f"Latency p50:   {p50:.2f} us")
        print(f"Latency p95:   {p95:.2f} us")
        print(f"Latency p99:   {p99:.2f} us")
        print(f"Latency p99.9: {p99_9:.2f} us")
        print(f"Projection rows: {proj.count_canonical()}")
        print(f"WAL next_offset: {engine.log.next_offset}")
        print(f"===========================================================\n")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    benchmark_full_pipeline(num_events=5000, fsync_policy="never")
    benchmark_full_pipeline(num_events=5000, fsync_policy="grouped_by_time")
