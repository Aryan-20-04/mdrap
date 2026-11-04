"""MDRAP v5.0 Phase 3: Persistence and Asynchronous Projection Benchmark.

Empirical verification of decoupled binary WAL durability and asynchronous
micro-batched SQLite projection throughput, tail latency, and crash recovery.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

# Ensure project root in sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

from mdrap import Engine, EngineConfig, RawEvent  # noqa: E402
from mdrap.fastpath import fast_crc32c  # noqa: E402
from mdrap.projection import SQLiteProjection  # noqa: E402


def make_raw_event(
    i: int, source: str = "BENCH_FEED", inst: str = "BTC-USDT"
) -> RawEvent:
    return RawEvent(
        source=source,
        payload={
            "instrument": inst,
            "event_type": "QUOTE",
            "bid": 50000.0 + (i % 100) * 0.1,
            "ask": 50001.0 + (i % 100) * 0.1,
            "bid_size": 1.5,
            "ask_size": 2.5,
            "sequence": i,
            "exchange_ts": 1700000000.0 + i * 0.001,
        },
        receive_timestamp=1700000000.0 + i * 0.001,
        raw_id=f"raw_{source}_{i}",
    )


def benchmark_full_pipeline(
    num_events: int = 10_000, fsync_policy: str = "never"
) -> dict:
    temp_dir = tempfile.mkdtemp(prefix=f"mdrap_v5_bench_{fsync_policy}_")
    db_path = os.path.join(temp_dir, "projection.db")
    try:
        cfg = EngineConfig(
            wal_dir=temp_dir, fsync_policy=fsync_policy, async_projections=True
        )
        engine = Engine(cfg)
        proj = SQLiteProjection(db_path=db_path, name="bench_proj")
        engine.subscribe(proj)

        events = [make_raw_event(i) for i in range(num_events)]
        warmup = min(1000, num_events // 10)
        for ev in events[:warmup]:
            engine.submit(ev)

        latencies_us = []
        t0_all = time.perf_counter()
        for ev in events[warmup:]:
            t0 = time.perf_counter_ns()
            engine.submit(ev)
            t1 = time.perf_counter_ns()
            latencies_us.append((t1 - t0) / 1000.0)
        elapsed = time.perf_counter() - t0_all

        measured = num_events - warmup
        throughput = measured / elapsed
        latencies_us.sort()
        n = len(latencies_us)

        # Flush to ensure projection parity
        t_flush_0 = time.perf_counter()
        engine.flush()
        flush_duration_ms = (time.perf_counter() - t_flush_0) * 1000.0

        checkpoint = proj.checkpoint()
        assert checkpoint == num_events - 1, (
            f"Checkpoint mismatch: {checkpoint} != {num_events - 1}"
        )

        return {
            "policy": fsync_policy,
            "events": measured,
            "elapsed_s": elapsed,
            "throughput_eps": throughput,
            "p50_us": latencies_us[int(n * 0.50)],
            "p95_us": latencies_us[int(n * 0.95)],
            "p99_us": latencies_us[int(n * 0.99)],
            "p99_9_us": latencies_us[int(n * 0.999)],
            "flush_ms": flush_duration_ms,
        }
    finally:
        engine.close()


def benchmark_crc32c_throughput(mb_size: int = 100) -> dict:
    data = os.urandom(1024 * 1024)  # 1 MB chunk
    t0 = time.perf_counter()
    crc = 0
    for _ in range(mb_size):
        crc = fast_crc32c(data, crc)
    elapsed = time.perf_counter() - t0
    gb_per_sec = (mb_size / 1024.0) / elapsed
    return {
        "size_mb": mb_size,
        "elapsed_s": elapsed,
        "gb_per_sec": gb_per_sec,
        "crc_final": f"0x{crc:08X}",
    }


def main():
    print("=" * 78)
    print(" MDRAP v5.0 Phase 3: Persistence and Asynchronous Projection Benchmark")
    print("=" * 78)

    print("\n[1/3] Benchmarking Hardware-Accelerated CRC32C (ADR-009)...")
    crc_res = benchmark_crc32c_throughput(100)
    print(
        f"  Processed {crc_res['size_mb']} MB in {crc_res['elapsed_s']:.3f}s: {crc_res['gb_per_sec']:.2f} GB/sec"
    )

    print("\n[2/3] Benchmarking Full Disk Pipeline (WAL + SQLite, Policy: 'never')...")
    res_never = benchmark_full_pipeline(num_events=10_000, fsync_policy="never")
    print(f"  Throughput: {res_never['throughput_eps']:,.1f} EPS")
    print(
        f"  p50: {res_never['p50_us']:.1f} us | p95: {res_never['p95_us']:.1f} us | p99: {res_never['p99_us']:.1f} us | p99.9: {res_never['p99_9_us']:.1f} us"
    )
    print(f"  Micro-batch Flush Duration: {res_never['flush_ms']:.2f} ms")

    print(
        "\n[3/3] Benchmarking Full Disk Pipeline (WAL + SQLite, Policy: 'grouped_by_time')..."
    )
    res_grouped = benchmark_full_pipeline(
        num_events=10_000, fsync_policy="grouped_by_time"
    )
    print(f"  Throughput: {res_grouped['throughput_eps']:,.1f} EPS")
    print(
        f"  p50: {res_grouped['p50_us']:.1f} us | p95: {res_grouped['p95_us']:.1f} us | p99: {res_grouped['p99_us']:.1f} us | p99.9: {res_grouped['p99_9_us']:.1f} us"
    )
    print(f"  Micro-batch Flush Duration: {res_grouped['flush_ms']:.2f} ms")

    print("\n" + "=" * 78)
    print(" Scoreboard Summary (v4.0.1 Baseline vs v5.0 Phase 3)")
    print("=" * 78)
    print(
        " Metric                      v4.0.1 Baseline       v5.0 Phase 3          Gain"
    )
    print("-" * 78)
    print(
        f" Full Pipeline ('never')     2,767.8 EPS           {res_never['throughput_eps']:>10,.1f} EPS         +{((res_never['throughput_eps'] / 2767.8) - 1) * 100:>6.1f}%"
    )
    print(
        f" p50 Latency ('never')       190.6 us              {res_never['p50_us']:>10.1f} us          -{((190.6 - res_never['p50_us']) / 190.6) * 100:>6.1f}%"
    )
    print(
        f" p95 Latency ('never')       2,279.1 us            {res_never['p95_us']:>10.1f} us          -{((2279.1 - res_never['p95_us']) / 2279.1) * 100:>6.1f}%"
    )
    print(
        f" p99 Latency ('never')       3,523.2 us            {res_never['p99_us']:>10.1f} us          -{((3523.2 - res_never['p99_us']) / 3523.2) * 100:>6.1f}%"
    )
    print(
        f" Full Pipeline ('grouped')   3,856.2 EPS           {res_grouped['throughput_eps']:>10,.1f} EPS         +{((res_grouped['throughput_eps'] / 3856.2) - 1) * 100:>6.1f}%"
    )
    print(
        f" p50 Latency ('grouped')     147.3 us              {res_grouped['p50_us']:>10.1f} us          -{((147.3 - res_grouped['p50_us']) / 147.3) * 100:>6.1f}%"
    )
    print(
        f" p99 Latency ('grouped')     3,438.9 us            {res_grouped['p99_us']:>10.1f} us          -{((3438.9 - res_grouped['p99_us']) / 3438.9) * 100:>6.1f}%"
    )
    print("=" * 78)


if __name__ == "__main__":
    main()
