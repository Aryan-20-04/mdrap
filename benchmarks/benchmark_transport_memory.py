"""MDRAP v5.0 Phase 4: Transport and Zero-Copy Memory Benchmark.

Empirical verification of SBE binary decoding, ITCH parser throughput,
and zero-copy Seqlock Shared Memory (SHM v3) IPC publish-to-read performance.
"""

from __future__ import annotations

import json
import os
import sys
import time

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mdrap.sbe import pack_sbe_tick, unpack_sbe_tick  # noqa: E402
from mdrap.itch import ITCHParser, ITCHSyntheticGenerator  # noqa: E402
from mdrap.shm import SHMWriter, SHMReader  # noqa: E402


def benchmark_sbe_vs_json(num_events: int = 100_000) -> dict:
    # 1. Prepare SBE message
    sbe_bytes = pack_sbe_tick(
        seq=1,
        symbol="AAPL",
        source="NASDAQ",
        price=150.25,
        size=100.0,
        bid=150.20,
        ask=150.30,
        bid_size=10.0,
        ask_size=20.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1700000000.0,
        ingest_ts=1700000000.001,
        broadcast_ts=1700000000.002,
        engine_us=1.5,
    )

    # 2. Prepare JSON message
    json_dict = {
        "seq": 1,
        "symbol": "AAPL",
        "source": "NASDAQ",
        "price": 150.25,
        "size": 100.0,
        "bid": 150.20,
        "ask": 150.30,
        "bid_size": 10.0,
        "ask_size": 20.0,
        "status_code": 1,
        "is_crossed": 0,
        "exchange_ts": 1700000000.0,
        "ingest_ts": 1700000000.001,
        "broadcast_ts": 1700000000.002,
        "engine_us": 1.5,
    }
    json_bytes = json.dumps(json_dict).encode("utf-8")

    # Time SBE unpack
    t0 = time.perf_counter()
    for _ in range(num_events):
        _ = unpack_sbe_tick(sbe_bytes)
    t_sbe = time.perf_counter() - t0
    sbe_eps = num_events / t_sbe

    # Time JSON unpack
    t0 = time.perf_counter()
    for _ in range(num_events):
        _ = json.loads(json_bytes)
    t_json = time.perf_counter() - t0
    json_eps = num_events / t_json

    return {
        "events": num_events,
        "sbe_eps": sbe_eps,
        "sbe_ns": (t_sbe / num_events) * 1e9,
        "json_eps": json_eps,
        "json_ns": (t_json / num_events) * 1e9,
        "speedup": sbe_eps / json_eps,
    }


def benchmark_itch_parser(num_messages: int = 100_000) -> dict:
    gen = ITCHSyntheticGenerator(seed=42)
    parser = ITCHParser()
    raw_frames = [gen.generate_frame() for _ in range(num_messages)]

    t0 = time.perf_counter()
    count = 0
    for frame in raw_frames:
        # frame layout: 2 bytes len, 1 byte type, body
        msg_type = frame[2:3]
        body = frame[3:]
        msg = parser.parse_payload(msg_type, body)
        if msg:
            count += 1
    elapsed = time.perf_counter() - t0
    return {
        "messages": count,
        "elapsed_s": elapsed,
        "throughput_eps": count / elapsed,
        "avg_ns": (elapsed / count) * 1e9,
    }


def benchmark_shm_ipc(num_events: int = 50_000) -> dict:
    shm_name = f"mdrap_bench_ipc_{os.getpid()}"
    writer = SHMWriter(shm_name, slot_count=65536)
    reader = SHMReader(shm_name)

    latencies_ns = []
    t0_all = time.perf_counter()

    for i in range(num_events):
        t0 = time.perf_counter_ns()
        writer.write_tick(
            seq=i,
            symbol="NVDA",
            source="BATS",
            price=120.50 + (i % 10) * 0.05,
            size=10.0,
            bid=120.45,
            ask=120.55,
            status="VALID",
            exchange_ts=1700000000.0,
        )
        slot = reader.read_slot(i)
        t1 = time.perf_counter_ns()
        assert slot is not None
        latencies_ns.append(t1 - t0)

    elapsed = time.perf_counter() - t0_all
    writer.close()
    reader.close()

    latencies_ns.sort()
    n = len(latencies_ns)
    return {
        "events": num_events,
        "elapsed_s": elapsed,
        "throughput_ops": num_events / elapsed,
        "p50_ns": latencies_ns[int(n * 0.50)],
        "p95_ns": latencies_ns[int(n * 0.95)],
        "p99_ns": latencies_ns[int(n * 0.99)],
        "p99_9_ns": latencies_ns[int(n * 0.999)],
    }


def main():
    print("=" * 78)
    print(" MDRAP v5.0 Phase 4: Transport and Zero-Copy Memory Benchmark")
    print("=" * 78)

    print("\n[1/3] Benchmarking Simple Binary Encoding (SBE) vs JSON Decoding...")
    sbe_res = benchmark_sbe_vs_json(100_000)
    print(f"  SBE Binary Unpack:   {sbe_res['sbe_eps']:>10,.1f} EPS  ({sbe_res['sbe_ns']:.1f} ns/msg)")
    print(f"  JSON Text Loads:     {sbe_res['json_eps']:>10,.1f} EPS  ({sbe_res['json_ns']:.1f} ns/msg)")
    print(f"  SBE Acceleration:    {sbe_res['speedup']:.2f}x speedup over JSON")

    print("\n[2/3] Benchmarking NASDAQ ITCH 5.0 Binary Wire Parser...")
    itch_res = benchmark_itch_parser(100_000)
    print(f"  Parsed {itch_res['messages']:,} messages in {itch_res['elapsed_s']:.3f}s: {itch_res['throughput_eps']:,.1f} msg/sec ({itch_res['avg_ns']:.1f} ns/msg)")

    print("\n[3/3] Benchmarking Zero-Copy Seqlock Shared Memory (SHM v3) IPC...")
    shm_res = benchmark_shm_ipc(50_000)
    print(f"  Throughput: {shm_res['throughput_ops']:,.1f} write+read roundtrips/sec")
    print(f"  p50 Latency: {shm_res['p50_ns']:.1f} ns | p95: {shm_res['p95_ns']:.1f} ns | p99: {shm_res['p99_ns']:.1f} ns | p99.9: {shm_res['p99_9_ns']:.1f} ns")

    print("\n" + "=" * 78)
    print(" Transport & Memory Summary")
    print("=" * 78)
    print(f" SBE Wire Decoding:              {sbe_res['sbe_eps']:>12,.1f} EPS  ({sbe_res['sbe_ns']:.1f} ns)")
    print(f" ITCH Protocol Parser:           {itch_res['throughput_eps']:>12,.1f} msg/sec ({itch_res['avg_ns']:.1f} ns)")
    print(f" SHM v3 IPC Publish-to-Read p50: {shm_res['p50_ns']:>12.1f} ns")
    print(f" SHM v3 IPC Publish-to-Read p99: {shm_res['p99_ns']:>12.1f} ns")
    print("=" * 78)


if __name__ == "__main__":
    main()
