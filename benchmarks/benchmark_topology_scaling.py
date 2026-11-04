"""Benchmark for Phase 2: Processing Topology & SPSC Scaling.

Measures:
1. SPSC Ring Buffer vs queue.Queue throughput and latency.
2. Channel Ingress Sequencer verification throughput.
3. Multi-shard fleet scaling (1 shard vs 2 shards vs 4 shards).
"""

from __future__ import annotations

import os
import sys
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
src_dir = os.path.join(repo_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from mdrap.models import RawEvent  # noqa: E402
from mdrap.partition import (  # noqa: E402
    ChannelIngressSequencer,
    FleetCoordinator,
    ShardConfig,
    SpscRingQueue,
    SymbolPartitioner,
)


def _make_event(seq: int, symbol: str) -> RawEvent:
    return RawEvent(
        source="NASDAQ",
        payload={
            "venue": "NASDAQ",
            "channel_id": "ITCH_CH1",
            "instrument": symbol,
            "event_type": "TRADE",
            "price": 150.0 + (seq % 100) * 0.01,
            "sequence": seq,
            "exchange_ts": 1700000000.0 + seq * 0.001,
        },
        receive_timestamp=1700000000.0 + seq * 0.001,
        raw_id=f"raw_{seq}",
    )


def bench_spsc_vs_std_queue(n_items: int = 100_000):
    import queue

    print(
        f"\n[1/3] Benchmarking SPSC Ring Buffer vs stdlib queue.Queue ({n_items:,} items)..."
    )

    # 1. stdlib queue.Queue
    q_std = queue.Queue(maxsize=n_items + 10)
    t0 = time.perf_counter()
    for i in range(n_items):
        q_std.put_nowait(i)
    for _ in range(n_items):
        _ = q_std.get_nowait()
    t_std = time.perf_counter() - t0
    eps_std = n_items / t_std

    # 2. SpscRingQueue
    q_spsc = SpscRingQueue(capacity=n_items + 10)
    t0 = time.perf_counter()
    for i in range(n_items):
        q_spsc.offer(i)
    for _ in range(n_items):
        _ = q_spsc.poll()
    t_spsc = time.perf_counter() - t0
    eps_spsc = n_items / t_spsc

    speedup = eps_spsc / eps_std
    print(f"  - stdlib queue.Queue: {eps_std:,.0f} ops/sec ({t_std:.4f}s)")
    print(f"  - SpscRingQueue:      {eps_spsc:,.0f} ops/sec ({t_spsc:.4f}s)")
    print(f"  - Speedup:            {speedup:.2f}x faster lock-free ring")


def bench_sequencer_throughput(n_events: int = 100_000):
    print(f"\n[2/3] Benchmarking ChannelIngressSequencer ({n_events:,} events)...")
    sequencer = ChannelIngressSequencer(channel_id="ITCH_CH1")
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA"]
    events = [_make_event(i + 1, symbols[i % len(symbols)]) for i in range(n_events)]

    t0 = time.perf_counter()
    for ev in events:
        st, r = sequencer.verify_sequence(ev)
    elapsed = time.perf_counter() - t0
    eps = n_events / elapsed
    avg_ns = (elapsed / n_events) * 1e9

    print(f"  - Verified Events:   {n_events:,}")
    print(f"  - Throughput:        {eps:,.0f} EPS")
    print(f"  - Average Latency:   {avg_ns:.1f} ns per verification")
    print(f"  - Gaps Detected:     {sequencer.gaps_detected}")
    print(f"  - Duplicates:        {sequencer.duplicates_detected}")


def bench_fleet_scaling(n_events: int = 50_000):
    print(f"\n[3/3] Benchmarking Multi-Shard Fleet Scaling ({n_events:,} events)...")
    symbols = ["AAPL", "GOOGL", "MSFT", "TSLA"]
    events = [_make_event(i + 1, symbols[i % len(symbols)]) for i in range(n_events)]

    for num_shards in [1, 2, 4]:
        partitioner = SymbolPartitioner(num_shards=num_shards, mode="hash")
        shard_configs = [
            ShardConfig(shard_id=i, name=f"Shard_{i}", max_queue_depth=100_000)
            for i in range(num_shards)
        ]
        fleet = FleetCoordinator(partitioner, shard_configs, use_spsc=True)
        fleet.start_fleet()

        t0 = time.perf_counter()
        for ev in events:
            sym = ev.payload["instrument"]
            fleet.dispatch_event(sym, b"dummy_payload_bytes_128")
        dispatch_elapsed = time.perf_counter() - t0

        # Wait for shards to drain
        timeout = 5.0
        t_wait_start = time.perf_counter()
        while time.perf_counter() - t_wait_start < timeout:
            health = fleet.fleet_health()
            if health["aggregate_events_processed"] >= n_events:
                break
            time.sleep(0.005)

        total_elapsed = time.perf_counter() - t0
        fleet.stop_fleet()

        dispatch_eps = n_events / dispatch_elapsed
        overall_eps = n_events / total_elapsed
        print(
            f"  - Shards: {num_shards} | Dispatch Rate: {dispatch_eps:,.0f} EPS | End-to-End Rate: {overall_eps:,.0f} EPS"
        )


if __name__ == "__main__":
    print("=" * 80)
    print("MDRAP v5.0 PHASE 2: PROCESSING TOPOLOGY & SPSC SCALING BENCHMARK")
    print("=" * 80)
    bench_spsc_vs_std_queue(100_000)
    bench_sequencer_throughput(100_000)
    bench_fleet_scaling(50_000)
    print("=" * 80)
