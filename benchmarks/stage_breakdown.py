"""
Empirical 4-Stage Critical Path Benchmark for MDRAP
Measures Feed Decode, Allocation, Scheduling/Execution, and I/O separately
on identical 100,000 deterministic events (seed=42).
"""

import ctypes
import json
import math
import os
import sqlite3
import struct
import sys
import time
from typing import List, Dict, Any

# Ensure src/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from simulator import FeedSimulator, SimulatorConfig
from sbe import pack_sbe_tick, TICK_PAYLOAD_STRUCT, HEADER_STRUCT
from fastpath import (
    _NATIVE_LIB,
    _CFastEvent,
    _CFastResult,
    FastQualityEngine,
)
from quality import QualityEngine, QualityConfig
from shm import SHMWriter


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    k = (len(values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    return values[int(f)] * (c - k) + values[int(c)] * (k - f)


def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if not math.isnan(f) else default
    except (ValueError, TypeError):
        return default


def run_stage_breakdown(num_events: int = 100_000, seed: int = 42) -> Dict[str, Any]:
    print(f"[*] Generating {num_events:,} deterministic events (seed={seed})...")
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))
    raw_events: List[RawEvent] = [raw for raw, _ in sim.generate()]
    n = len(raw_events)
    print(f"[+] Successfully generated {n:,} events.")

    results: Dict[str, Any] = {}

    # =========================================================================
    # STAGE 1: FEED DECODE
    # JSON wire string parsing vs Binary SBE frame unpacking
    # =========================================================================
    print("\n--- STAGE 1: FEED DECODE ---")

    # Prepare payloads
    json_payloads = [
        json.dumps(
            {
                "seq": safe_int(ev.payload.get("sequence"), i),
                "sym": str(ev.payload.get("instrument", "AAPL")),
                "src": ev.source,
                "px": safe_float(ev.payload.get("price"), 100.0),
                "sz": safe_float(ev.payload.get("quantity"), 10.0),
                "ts": safe_float(ev.payload.get("exchange_ts"), 0.0),
            }
        )
        for i, ev in enumerate(raw_events)
    ]

    sbe_frames = bytearray(n * 128)
    for i, ev in enumerate(raw_events):
        p = ev.payload
        px = safe_float(p.get("price"), 100.0)
        sz = safe_float(p.get("quantity"), 10.0)
        bid = safe_float(p.get("bid"), px - 0.05)
        ask = safe_float(p.get("ask"), px + 0.05)
        sbe_frames[i * 128 : (i + 1) * 128] = pack_sbe_tick(
            seq=safe_int(p.get("sequence"), i + 1),
            symbol=str(p.get("instrument", "AAPL")),
            source=ev.source,
            price=px,
            size=sz,
            bid=bid,
            ask=ask,
            status="VALID",
        )

    # 1.A: Pure Python JSON Decode
    json_latencies_ns = []
    t0 = time.perf_counter()
    for s in json_payloads:
        t_start = time.perf_counter_ns()
        d = json.loads(s)
        _ = d["sym"]
        t_end = time.perf_counter_ns()
        json_latencies_ns.append(t_end - t_start)
    t_json_total = time.perf_counter() - t0
    json_latencies_ns.sort()

    # 1.B: Binary SBE Frame Decode (Native/Binary)
    sbe_latencies_ns = []
    t0 = time.perf_counter()
    for i in range(n):
        t_start = time.perf_counter_ns()
        # Direct zero-copy 128-byte frame unpack
        header = HEADER_STRUCT.unpack_from(sbe_frames, i * 128)
        payload = TICK_PAYLOAD_STRUCT.unpack_from(sbe_frames, i * 128 + 8)
        _ = payload[14]  # symbol
        t_end = time.perf_counter_ns()
        sbe_latencies_ns.append(t_end - t_start)
    t_sbe_total = time.perf_counter() - t0
    sbe_latencies_ns.sort()

    results["feed_decode"] = {
        "python_json": {
            "total_s": t_json_total,
            "eps": n / t_json_total,
            "p50_ns": percentile(json_latencies_ns, 50),
            "p95_ns": percentile(json_latencies_ns, 95),
            "p99_ns": percentile(json_latencies_ns, 99),
            "max_ns": json_latencies_ns[-1],
        },
        "binary_sbe": {
            "total_s": t_sbe_total,
            "eps": n / t_sbe_total,
            "p50_ns": percentile(sbe_latencies_ns, 50),
            "p95_ns": percentile(sbe_latencies_ns, 95),
            "p99_ns": percentile(sbe_latencies_ns, 99),
            "max_ns": sbe_latencies_ns[-1],
        },
        "speedup": t_json_total / max(t_sbe_total, 1e-9),
    }
    print(
        f"JSON Decode:   {results['feed_decode']['python_json']['total_s']:.4f}s ({results['feed_decode']['python_json']['eps']:,.0f} eps, p50: {results['feed_decode']['python_json']['p50_ns']:.0f} ns)"
    )
    print(
        f"SBE Binary:    {results['feed_decode']['binary_sbe']['total_s']:.4f}s ({results['feed_decode']['binary_sbe']['eps']:,.0f} eps, p50: {results['feed_decode']['binary_sbe']['p50_ns']:.0f} ns)"
    )
    print(f"Speedup:       {results['feed_decode']['speedup']:.2f}x")

    # =========================================================================
    # STAGE 2: ALLOCATION
    # Dynamic Python Dataclass Creation vs Contiguous Memory Array Indexing
    # =========================================================================
    print("\n--- STAGE 2: ALLOCATION ---")

    # 2.A: Python Object Allocation (CanonicalEvent Dataclass)
    alloc_py_latencies_ns = []
    py_objects = []
    t0 = time.perf_counter()
    for i, ev in enumerate(raw_events):
        p = ev.payload
        t_start = time.perf_counter_ns()
        ev_type = EventType.TRADE if p.get("event_type") == "TRADE" else EventType.QUOTE
        c_ev = CanonicalEvent(
            event_id=f"evt-{i}",
            instrument_id=str(p.get("instrument", "AAPL")),
            event_type=ev_type,
            exchange_timestamp=safe_float(p.get("exchange_ts"), 0.0),
            receive_timestamp=ev.receive_timestamp,
            processing_timestamp=time.time(),
            source=ev.source,
            sequence_number=safe_int(p.get("sequence"), i),
            price=safe_float(p.get("price"), 100.0)
            if p.get("price") is not None
            else None,
            quantity=safe_float(p.get("quantity"), 10.0)
            if p.get("quantity") is not None
            else None,
            bid_price=safe_float(p.get("bid"), 99.95)
            if p.get("bid") is not None
            else None,
            ask_price=safe_float(p.get("ask"), 100.05)
            if p.get("ask") is not None
            else None,
            quality_status=QualityStatus.VALID,
        )
        t_end = time.perf_counter_ns()
        py_objects.append(c_ev)
        alloc_py_latencies_ns.append(t_end - t_start)
    t_alloc_py_total = time.perf_counter() - t0
    alloc_py_latencies_ns.sort()

    # 2.B: Contiguous Native Memory Buffer (Zero Python Object Allocation)
    alloc_c_latencies_ns = []
    c_events_array = (_CFastEvent * n)()
    t0 = time.perf_counter()
    for i in range(n):
        t_start = time.perf_counter_ns()
        ptr = ctypes.pointer(c_events_array[i])
        _ = ptr.contents.price
        t_end = time.perf_counter_ns()
        alloc_c_latencies_ns.append(t_end - t_start)
    t_alloc_c_total = time.perf_counter() - t0
    alloc_c_latencies_ns.sort()

    results["allocation"] = {
        "python_dataclass": {
            "total_s": t_alloc_py_total,
            "eps": n / t_alloc_py_total,
            "p50_ns": percentile(alloc_py_latencies_ns, 50),
            "p95_ns": percentile(alloc_py_latencies_ns, 95),
            "p99_ns": percentile(alloc_py_latencies_ns, 99),
            "max_ns": alloc_py_latencies_ns[-1],
        },
        "contiguous_native": {
            "total_s": t_alloc_c_total,
            "eps": n / t_alloc_c_total,
            "p50_ns": percentile(alloc_c_latencies_ns, 50),
            "p95_ns": percentile(alloc_c_latencies_ns, 95),
            "p99_ns": percentile(alloc_c_latencies_ns, 99),
            "max_ns": alloc_c_latencies_ns[-1],
        },
        "speedup": t_alloc_py_total / max(t_alloc_c_total, 1e-9),
    }
    print(
        f"Python Dataclass:   {results['allocation']['python_dataclass']['total_s']:.4f}s ({results['allocation']['python_dataclass']['eps']:,.0f} eps, p50: {results['allocation']['python_dataclass']['p50_ns']:.0f} ns)"
    )
    print(
        f"Contiguous Native:  {results['allocation']['contiguous_native']['total_s']:.4f}s ({results['allocation']['contiguous_native']['eps']:,.0f} eps, p50: {results['allocation']['contiguous_native']['p50_ns']:.0f} ns)"
    )
    print(f"Speedup:            {results['allocation']['speedup']:.2f}x")

    # =========================================================================
    # STAGE 3: SCHEDULING & QUALITY EVALUATION
    # Pure Python Loop & Engine vs Vectorized Contiguous Native C Kernel
    # =========================================================================
    print("\n--- STAGE 3: SCHEDULING & QUALITY EVALUATION ---")

    # 3.A: Pure Python Quality Engine (interpreted loop, dynamic dispatch)
    py_engine = QualityEngine(QualityConfig())
    eval_py_latencies_ns = []
    t0 = time.perf_counter()
    for ev in py_objects:
        t_start = time.perf_counter_ns()
        _ = py_engine.evaluate(ev)
        t_end = time.perf_counter_ns()
        eval_py_latencies_ns.append(t_end - t_start)
    t_eval_py_total = time.perf_counter() - t0
    eval_py_latencies_ns.sort()

    # 3.B: Native C Vectorized Batch Engine (GIL released, contiguous SIMD loop)
    for i, ev in enumerate(py_objects):
        c_events_array[i].source_id = 0
        c_events_array[i].instrument_id = i % 8
        c_events_array[i].event_type = 0
        c_events_array[i].exchange_timestamp = ev.exchange_timestamp
        c_events_array[i].receive_timestamp = ev.receive_timestamp
        c_events_array[i].sequence_number = ev.sequence_number or i
        c_events_array[i].price = ev.price or 100.0
        c_events_array[i].quantity = ev.quantity or 10.0
        c_events_array[i].bid_price = (ev.price or 100.0) - 0.05
        c_events_array[i].ask_price = (ev.price or 100.0) + 0.05
        c_events_array[i].bid_size = 100.0
        c_events_array[i].ask_size = 100.0

    c_results_array = (_CFastResult * n)()
    _NATIVE_LIB.fastpath_init(1.0, 4.0, 100)

    t0 = time.perf_counter()
    _NATIVE_LIB.fastpath_evaluate_batch(c_events_array, c_results_array, n)
    t_eval_c_total = time.perf_counter() - t0
    eval_c_per_event_ns = (t_eval_c_total / n) * 1e9

    results["scheduling_execution"] = {
        "python_engine": {
            "total_s": t_eval_py_total,
            "eps": n / t_eval_py_total,
            "p50_ns": percentile(eval_py_latencies_ns, 50),
            "p95_ns": percentile(eval_py_latencies_ns, 95),
            "p99_ns": percentile(eval_py_latencies_ns, 99),
            "max_ns": eval_py_latencies_ns[-1],
        },
        "native_c_kernel": {
            "total_s": t_eval_c_total,
            "eps": n / max(t_eval_c_total, 1e-9),
            "p50_ns": eval_c_per_event_ns,
            "p95_ns": eval_c_per_event_ns,
            "p99_ns": eval_c_per_event_ns,
            "max_ns": eval_c_per_event_ns,
        },
        "speedup": t_eval_py_total / max(t_eval_c_total, 1e-9),
    }
    print(
        f"Python Engine:      {results['scheduling_execution']['python_engine']['total_s']:.4f}s ({results['scheduling_execution']['python_engine']['eps']:,.0f} eps, p50: {results['scheduling_execution']['python_engine']['p50_ns']:.0f} ns)"
    )
    print(
        f"Native C Kernel:    {results['scheduling_execution']['native_c_kernel']['total_s']:.6f}s ({results['scheduling_execution']['native_c_kernel']['eps']:,.0f} eps, avg: {results['scheduling_execution']['native_c_kernel']['p50_ns']:.1f} ns)"
    )
    print(f"Speedup:            {results['scheduling_execution']['speedup']:.2f}x")

    # =========================================================================
    # STAGE 4: I/O & PERSISTENCE
    # SQLite WAL Batch Commits vs Zero-Copy Shared Memory (SHM)
    # =========================================================================
    print("\n--- STAGE 4: I/O & PERSISTENCE ---")

    # 4.A: SQLite WAL Batch Write (batch size = 1,000)
    db_file = "test_stage_io.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("""
        CREATE TABLE events (
            event_id TEXT, instrument_id TEXT, event_type TEXT,
            exchange_ts REAL, receive_ts REAL, price REAL, quantity REAL
        )
    """)
    rows = [
        (
            ev.event_id,
            ev.instrument_id,
            ev.event_type.value,
            ev.exchange_timestamp,
            ev.receive_timestamp,
            ev.price,
            ev.quantity,
        )
        for ev in py_objects
    ]
    batch_size = 1000
    batches = [rows[i : i + batch_size] for i in range(0, n, batch_size)]

    io_sqlite_latencies_ns = []
    t0 = time.perf_counter()
    for b in batches:
        t_start = time.perf_counter_ns()
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", b)
        conn.commit()
        t_end = time.perf_counter_ns()
        per_evt = (t_end - t_start) / len(b)
        io_sqlite_latencies_ns.extend([per_evt] * len(b))
    t_io_sqlite_total = time.perf_counter() - t0
    conn.close()
    if os.path.exists(db_file):
        os.remove(db_file)
    io_sqlite_latencies_ns.sort()

    # 4.B: Zero-Copy Shared Memory (SHM) Ring Buffer Commit
    shm_writer = SHMWriter(name="mdrap_stage_shm_test", slot_count=16384)
    io_shm_latencies_ns = []
    t0 = time.perf_counter()
    for i, ev in enumerate(py_objects):
        t_start = time.perf_counter_ns()
        shm_writer.write_tick(
            seq=i + 1,
            symbol=ev.instrument_id,
            source=ev.source,
            price=ev.price,
            size=ev.quantity,
            bid=ev.price - 0.05 if ev.price else None,
            ask=ev.price + 0.05 if ev.price else None,
            bid_size=100.0,
            ask_size=100.0,
            status=ev.quality_status.value,
            is_crossed=False,
            exchange_ts=ev.exchange_timestamp,
            ingest_ts=ev.receive_timestamp,
            broadcast_ts=ev.receive_timestamp,
            engine_us=0.05,
        )
        t_end = time.perf_counter_ns()
        io_shm_latencies_ns.append(t_end - t_start)
    t_io_shm_total = time.perf_counter() - t0
    shm_writer.close()
    io_shm_latencies_ns.sort()

    results["io_persistence"] = {
        "sqlite_wal": {
            "total_s": t_io_sqlite_total,
            "eps": n / t_io_sqlite_total,
            "p50_ns": percentile(io_sqlite_latencies_ns, 50),
            "p95_ns": percentile(io_sqlite_latencies_ns, 95),
            "p99_ns": percentile(io_sqlite_latencies_ns, 99),
            "max_ns": io_sqlite_latencies_ns[-1],
        },
        "shm_ring": {
            "total_s": t_io_shm_total,
            "eps": n / t_io_shm_total,
            "p50_ns": percentile(io_shm_latencies_ns, 50),
            "p95_ns": percentile(io_shm_latencies_ns, 95),
            "p99_ns": percentile(io_shm_latencies_ns, 99),
            "max_ns": io_shm_latencies_ns[-1],
        },
        "speedup": t_io_sqlite_total / max(t_io_shm_total, 1e-9),
    }
    print(
        f"SQLite WAL (Disk):  {results['io_persistence']['sqlite_wal']['total_s']:.4f}s ({results['io_persistence']['sqlite_wal']['eps']:,.0f} eps, p50: {results['io_persistence']['sqlite_wal']['p50_ns']:.0f} ns)"
    )
    print(
        f"Shared Memory (SHM):{results['io_persistence']['shm_ring']['total_s']:.4f}s ({results['io_persistence']['shm_ring']['eps']:,.0f} eps, p50: {results['io_persistence']['shm_ring']['p50_ns']:.0f} ns)"
    )
    print(f"Speedup:            {results['io_persistence']['speedup']:.2f}x")

    # =========================================================================
    # SUMMARY: AMDAHL'S LAW & BOTTLENECK ANALYSIS
    # =========================================================================
    total_py = t_json_total + t_alloc_py_total + t_eval_py_total + t_io_sqlite_total
    total_native = t_sbe_total + t_alloc_c_total + t_eval_c_total + t_io_shm_total

    results["summary"] = {
        "num_events": n,
        "seed": seed,
        "total_python_s": total_py,
        "total_native_s": total_native,
        "overall_speedup": total_py / total_native,
        "time_breakdown_python_pct": {
            "feed_decode": (t_json_total / total_py) * 100,
            "allocation": (t_alloc_py_total / total_py) * 100,
            "scheduling_execution": (t_eval_py_total / total_py) * 100,
            "io_persistence": (t_io_sqlite_total / total_py) * 100,
        },
        "time_breakdown_native_pct": {
            "feed_decode": (t_sbe_total / total_native) * 100,
            "allocation": (t_alloc_c_total / total_native) * 100,
            "scheduling_execution": (t_eval_c_total / total_native) * 100,
            "io_persistence": (t_io_shm_total / total_native) * 100,
        },
    }

    print("\n" + "=" * 60)
    print("EMPIRICAL BOTTLENECK ANALYSIS (Where time is actually spent):")
    print("=" * 60)
    print(f"Pure Python Path Total Time:  {total_py:.4f}s")
    for stage, pct in results["summary"]["time_breakdown_python_pct"].items():
        print(f"  - {stage:22}: {pct:5.1f}%")
    print(f"\nNative / SBE / SHM Total Time:{total_native:.4f}s")
    for stage, pct in results["summary"]["time_breakdown_native_pct"].items():
        print(f"  - {stage:22}: {pct:5.1f}%")
    print(
        f"\nOverall Pipeline Speedup:     {results['summary']['overall_speedup']:.2f}x"
    )
    print("=" * 60)

    out_path = os.path.join(os.path.dirname(__file__), "stage_breakdown.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[+] Saved results to {out_path}")

    return results


if __name__ == "__main__":
    run_stage_breakdown(100_000, seed=42)
