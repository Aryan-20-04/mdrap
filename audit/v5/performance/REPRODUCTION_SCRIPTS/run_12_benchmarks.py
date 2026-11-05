"""MDRAP v5.0: Authoritative 12-Tier Performance Benchmark Suite.

Executes and measures:
 1. Native CRC32C (Hardware SSE4.2 vs fallback)
 2. Native Quality Processing (FastQualityEngine batch)
 3. Python/C FFI Crossing Overhead
 4. NASDAQ ITCH 5.0 Wire Protocol Parsing
 5. Simple Binary Encoding (SBE) Zero-Copy Wire Decoding
 6. Lock-Free SPSC Ring Queue Concurrent Transfer
 7. In-Memory Engine (Validation + Dedup + Reconcile)
 8. WAL Append Throughput (Buffered IngestLog)
 9. WAL Durability Barrier (IMMEDIATE Physical Fsync)
10. SQLite Analytical Projections (Micro-Batched Coalescing)
11. Shared Memory (SHM v3) IPC Delivery (Publish-to-Read)
12. Full End-to-End Processing (Ingest -> Quality -> Reconcile -> WAL -> Proj -> SHM)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import Any

# Ensure src in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "src")))

import psutil

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.engine import Engine, EngineConfig
from mdrap.fastpath import FastQualityEngine, fast_crc32c
from mdrap.itch import ITCHParser, ITCHSyntheticGenerator
from mdrap.sbe import pack_sbe_tick, unpack_sbe_tick
from mdrap.partition import SpscRingQueue
from mdrap.ingestlog import IngestLog
from mdrap.projection import SQLiteProjection, ProjectionTracker
from mdrap.shm import SHMWriter, SHMReader


def get_process_memory_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def compute_latencies_us(samples_ns: list[int]) -> dict[str, float]:
    if not samples_ns:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "p99_9": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}
    s = sorted(samples_ns)
    n = len(s)
    return {
        "p50": s[int(n * 0.50)] / 1000.0,
        "p95": s[int(n * 0.95)] / 1000.0,
        "p99": s[int(n * 0.99)] / 1000.0,
        "p99_9": s[int(n * 0.999)] / 1000.0,
        "min": s[0] / 1000.0,
        "max": s[-1] / 1000.0,
        "mean": (sum(s) / n) / 1000.0,
    }


# ---------------------------------------------------------------------------
# 1. Native CRC32C
# ---------------------------------------------------------------------------
def bench_crc32c(iterations: int = 100_000, buffer_size: int = 1024) -> dict[str, Any]:
    buf = b"X" * buffer_size
    # Warmup
    for _ in range(1000):
        _ = fast_crc32c(buf)

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        st = time.perf_counter_ns()
        _ = fast_crc32c(buf)
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    total_bytes = iterations * buffer_size
    gb_per_sec = (total_bytes / (1024**3)) / total_time_s
    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "Native CRC32C",
        "iterations": iterations,
        "buffer_size_bytes": buffer_size,
        "total_gb": total_bytes / (1024**3),
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "throughput_gb_sec": gb_per_sec,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 2. Native Quality Processing (FastQualityEngine)
# ---------------------------------------------------------------------------
def bench_native_processing(iterations: int = 100_000) -> dict[str, Any]:
    engine = FastQualityEngine()
    events = [
        CanonicalEvent(
            event_id=f"e_{i}",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=1700000000.0 + i * 0.001,
            receive_timestamp=1700000000.0 + i * 0.001,
            processing_timestamp=1700000000.0 + i * 0.001,
            source="NASDAQ",
            sequence_number=i + 1,
            price=150.25 + (i % 5) * 0.01,
            quantity=100.0,
            bid_price=150.20,
            ask_price=150.30,
            quality_status=QualityStatus.VALID,
        )
        for i in range(1000)
    ]

    # Warmup
    for _ in range(10):
        _ = engine.evaluate_batch(events)

    latencies_ns = []
    batch_size = 1000
    num_batches = iterations // batch_size
    t0 = time.perf_counter_ns()
    for _ in range(num_batches):
        st = time.perf_counter_ns()
        _ = engine.evaluate_batch(events)
        elapsed_batch = time.perf_counter_ns() - st
        per_event_ns = elapsed_batch // batch_size
        latencies_ns.extend([per_event_ns] * batch_size)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "Native Processing (Batch)",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 3. Python/C FFI Crossing Overhead
# ---------------------------------------------------------------------------
def bench_ffi_crossing(iterations: int = 50_000) -> dict[str, Any]:
    engine = FastQualityEngine()
    ev = CanonicalEvent(
        event_id="e_single",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.0,
        processing_timestamp=1700000000.0,
        source="NASDAQ",
        sequence_number=1,
        price=150.25,
        quantity=100.0,
        bid_price=150.20,
        ask_price=150.30,
        quality_status=QualityStatus.VALID,
    )

    for _ in range(1000):
        _ = engine.evaluate(ev)

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        st = time.perf_counter_ns()
        _ = engine.evaluate(ev)
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "Python/C FFI (Single Event)",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 4. ITCH 5.0 Wire Protocol Parsing
# ---------------------------------------------------------------------------
def bench_itch_decoding(iterations: int = 100_000) -> dict[str, Any]:
    gen = ITCHSyntheticGenerator(seed=42)
    parser = ITCHParser()
    frames = [gen.generate_frame() for _ in range(iterations)]

    # Warmup
    for f in frames[:1000]:
        _ = parser.parse_payload(f[2:3], f[3:])

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for f in frames:
        st = time.perf_counter_ns()
        _ = parser.parse_payload(f[2:3], f[3:])
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "ITCH 5.0 Wire Parser",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 5. Simple Binary Encoding (SBE) Zero-Copy Wire Decoding
# ---------------------------------------------------------------------------
def bench_sbe_decoding(iterations: int = 100_000) -> dict[str, Any]:
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

    for _ in range(1000):
        _ = unpack_sbe_tick(sbe_bytes)

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        st = time.perf_counter_ns()
        _ = unpack_sbe_tick(sbe_bytes)
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "SBE Wire Decoding",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 6. Lock-Free SPSC Ring Queue
# ---------------------------------------------------------------------------
def bench_spsc_queue(iterations: int = 100_000) -> dict[str, Any]:
    q = SpscRingQueue(capacity=65536)
    received = []
    done = threading.Event()

    def consumer():
        count = 0
        while count < iterations:
            item = q.poll()
            if item is not None:
                received.append(item)
                count += 1
            else:
                time.sleep(0.00001)
        done.set()

    t_cons = threading.Thread(target=consumer, daemon=True)
    t_cons.start()

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for i in range(iterations):
        st = time.perf_counter_ns()
        while not q.offer(i):
            time.sleep(0.00001)
        latencies_ns.append(time.perf_counter_ns() - st)

    done.wait(timeout=5.0)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "SPSC Lock-Free Ring Queue",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 7. In-Memory Engine
# ---------------------------------------------------------------------------
def bench_in_memory_engine(iterations: int = 50_000) -> dict[str, Any]:
    engine = Engine(path=None)
    raw_events = [
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "price": 150.25 + (i % 5) * 0.05,
                "size": 100.0,
                "bid": 150.20,
                "ask": 150.30,
                "sequence": i + 1,
                "timestamp": 1700000000.0 + i * 0.001,
            },
        )
        for i in range(iterations)
    ]

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for ev in raw_events:
        st = time.perf_counter_ns()
        _ = engine.submit(ev)
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "In-Memory Engine",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 8. WAL Append Throughput (Buffered)
# ---------------------------------------------------------------------------
def bench_wal_append(iterations: int = 50_000) -> dict[str, Any]:
    td = tempfile.mkdtemp(prefix="mdrap_wal_bench_")
    log_path = os.path.join(td, "ingest.wal")
    log = IngestLog(log_path, fsync_policy="never")

    events = [{"seq": i, "sym": "AAPL", "p": 150.25} for i in range(iterations)]

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for ev in events:
        st = time.perf_counter_ns()
        _ = log.append(ev)
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9
    log.close()
    shutil.rmtree(td, ignore_errors=True)

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "WAL Append (Buffered)",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 9. WAL Durability Barrier (IMMEDIATE Fsync)
# ---------------------------------------------------------------------------
def bench_wal_durability_barrier(iterations: int = 2_000) -> dict[str, Any]:
    td = tempfile.mkdtemp(prefix="mdrap_fsync_bench_")
    log_path = os.path.join(td, "ingest_sync.wal")
    log = IngestLog(log_path, fsync_policy="always")

    events = [{"seq": i, "sym": "AAPL", "p": 150.25} for i in range(iterations)]

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for ev in events:
        st = time.perf_counter_ns()
        _ = log.append(ev)
        latencies_ns.append(time.perf_counter_ns() - st)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9
    log.close()
    shutil.rmtree(td, ignore_errors=True)

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "WAL Durability Barrier (IMMEDIATE Fsync)",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 10. SQLite Analytical Projections (Micro-Batched Coalescing)
# ---------------------------------------------------------------------------
def bench_sqlite_projections(iterations: int = 20_000) -> dict[str, Any]:
    td = tempfile.mkdtemp(prefix="mdrap_sqlite_bench_")
    db_path = os.path.join(td, "proj.db")
    proj = SQLiteProjection(db_path)
    tracker = ProjectionTracker(proj, name="bench_proj", async_drain=True)
    tracker.start_worker()

    events = [
        CanonicalEvent(
            event_id=f"e_{i}",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=1700000000.0 + i * 0.001,
            receive_timestamp=1700000000.0 + i * 0.001,
            processing_timestamp=1700000000.0 + i * 0.001,
            source="NASDAQ",
            sequence_number=i + 1,
            price=150.25 + (i % 5) * 0.01,
            quantity=100.0,
            bid_price=150.20,
            ask_price=150.30,
            quality_status=QualityStatus.VALID,
        )
        for i in range(iterations)
    ]

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for i, ev in enumerate(events):
        st = time.perf_counter_ns()
        tracker.enqueue([ev], i)
        latencies_ns.append(time.perf_counter_ns() - st)

    tracker.flush(timeout=10.0)
    total_time_s = (time.perf_counter_ns() - t0) / 1e9
    tracker.close()
    shutil.rmtree(td, ignore_errors=True)

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "SQLite Projection (Micro-Batched)",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 11. Shared Memory (SHM v3) IPC Delivery
# ---------------------------------------------------------------------------
def bench_shm_delivery(iterations: int = 50_000) -> dict[str, Any]:
    shm_name = f"mdrap_audit_shm_{os.getpid()}"
    writer = SHMWriter(shm_name, slot_count=65536)
    reader = SHMReader(shm_name)

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for i in range(iterations):
        st = time.perf_counter_ns()
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
        latencies_ns.append(time.perf_counter_ns() - st)
        assert slot is not None

    total_time_s = (time.perf_counter_ns() - t0) / 1e9
    writer.close()
    reader.close()

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "SHM v3 IPC Delivery",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


# ---------------------------------------------------------------------------
# 12. Full End-to-End Processing
# ---------------------------------------------------------------------------
def bench_full_end_to_end(iterations: int = 20_000) -> dict[str, Any]:
    td = tempfile.mkdtemp(prefix="mdrap_e2e_bench_")
    db_path = os.path.join(td, "canonical.db")
    proj = SQLiteProjection(db_path)
    engine = Engine(
        path=td,
        fsync_policy="never",
        async_projections=True,
    )
    engine.subscribe(proj)

    raw_events = [
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "price": 150.25 + (i % 5) * 0.05,
                "size": 100.0,
                "bid": 150.20,
                "ask": 150.30,
                "sequence": i + 1,
                "timestamp": 1700000000.0 + i * 0.001,
            },
        )
        for i in range(iterations)
    ]

    latencies_ns = []
    t0 = time.perf_counter_ns()
    for ev in raw_events:
        st = time.perf_counter_ns()
        _ = engine.submit(ev)
        latencies_ns.append(time.perf_counter_ns() - st)

    engine.flush()
    total_time_s = (time.perf_counter_ns() - t0) / 1e9
    engine.close()
    shutil.rmtree(td, ignore_errors=True)

    lat = compute_latencies_us(latencies_ns)
    return {
        "name": "Full End-to-End Pipeline",
        "iterations": iterations,
        "elapsed_s": total_time_s,
        "eps": iterations / total_time_s,
        "latencies_us": lat,
    }


def main():
    print("=" * 78)
    print(" MDRAP v5.0 Authoritative 12-Tier Performance Benchmark Suite")
    print("=" * 78)
    print(f" CPU: {psutil.cpu_count(logical=False)} physical / {psutil.cpu_count(logical=True)} logical cores")
    print(f" RAM: {psutil.virtual_memory().total / (1024**3):.1f} GB total")
    print(f" Start Memory RSS: {get_process_memory_mb():.2f} MB")
    print("=" * 78)

    results = []

    benchmarks = [
        ("Tier 01: Native CRC32C", lambda: bench_crc32c(100_000, 1024)),
        ("Tier 02: Native Processing", lambda: bench_native_processing(100_000)),
        ("Tier 03: Python/C FFI Crossing", lambda: bench_ffi_crossing(50_000)),
        ("Tier 04: ITCH 5.0 Wire Parser", lambda: bench_itch_decoding(100_000)),
        ("Tier 05: SBE Wire Decoding", lambda: bench_sbe_decoding(100_000)),
        ("Tier 06: SPSC Ring Queue", lambda: bench_spsc_queue(100_000)),
        ("Tier 07: In-Memory Engine", lambda: bench_in_memory_engine(50_000)),
        ("Tier 08: WAL Append (Buffered)", lambda: bench_wal_append(50_000)),
        ("Tier 09: WAL Durability Barrier", lambda: bench_wal_durability_barrier(2_000)),
        ("Tier 10: SQLite Projections", lambda: bench_sqlite_projections(20_000)),
        ("Tier 11: SHM v3 IPC Delivery", lambda: bench_shm_delivery(50_000)),
        ("Tier 12: Full End-to-End Pipeline", lambda: bench_full_end_to_end(20_000)),
    ]

    for label, fn in benchmarks:
        print(f"\nRunning {label}...")
        res = fn()
        results.append(res)
        lat = res["latencies_us"]
        if "throughput_gb_sec" in res:
            print(f"  Throughput:  {res['throughput_gb_sec']:.2f} GB/sec ({res['eps']:,.1f} ops/sec)")
        else:
            print(f"  Throughput:  {res['eps']:>12,.1f} EPS (in {res['elapsed_s']:.3f}s)")
        print(f"  Latencies:   p50: {lat['p50']:>7.2f} us | p95: {lat['p95']:>7.2f} us | p99: {lat['p99']:>7.2f} us | p99.9: {lat['p99_9']:>7.2f} us")

    end_mem = get_process_memory_mb()
    print(f"\nEnd Memory RSS: {end_mem:.2f} MB")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "RAW_BENCHMARK_RESULTS")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "benchmark_12_tiers_raw.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.time(),
                "system": {
                    "cpu_logical": psutil.cpu_count(logical=True),
                    "cpu_physical": psutil.cpu_count(logical=False),
                    "ram_gb": psutil.virtual_memory().total / (1024**3),
                    "start_rss_mb": get_process_memory_mb(),
                    "end_rss_mb": end_mem,
                },
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"\nSaved raw results to: {out_file}")


if __name__ == "__main__":
    main()
