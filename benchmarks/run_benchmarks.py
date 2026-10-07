"""MDRAP End-to-End Performance and Durability Benchmark Suite (Spec §8, Phase 6).

Measures real institutional pipeline throughput, tail latencies, memory footprint,
and recovery time across all 5 core architecture components:
1. Python pipeline (pure fold state transitions)
2. Native pipeline (C fastpath kernel or parity fallback)
3. IngestLog (segmented durable write-ahead log append & recovery)
4. SQLite Projection (atomic row + checkpoint commit)
5. End-to-end Engine (WAL ingest -> quality evaluation -> projection)

Every performance claim traces to reproducible timed runs with seed=42.
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
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mdrap.clock import SystemClock
from mdrap.engine import Engine, EngineConfig
from mdrap.ingestlog import IngestLog
from mdrap.metrics import get_rss_mb, percentile
from mdrap.models import RawEvent
from mdrap.projection import SQLiteProjection
from mdrap.simulator import FeedSimulator, SimulatorConfig


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
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
    }


# ---------------------------------------------------------------------------
# 1. Pure Python Pipeline Benchmark
# ---------------------------------------------------------------------------
def benchmark_python_pipeline(num_events: int = 20_000, seed: int = 42) -> dict[str, Any]:
    """Measure pure Python in-memory state transition throughput and latencies."""
    engine = Engine()
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))
    clock = SystemClock()

    latencies_us = []
    gc.collect()
    rss_before = get_rss_mb()

    t0 = time.perf_counter()
    state = engine.state
    for raw, _ in sim.generate():
        t_event_0 = time.perf_counter()
        state, _ = engine.step(state, raw, clock)
        latencies_us.append((time.perf_counter() - t_event_0) * 1_000_000)
    t1 = time.perf_counter()

    elapsed = max(0.0001, t1 - t0)
    rss_after = get_rss_mb()
    engine.close()

    sorted_lats = sorted(latencies_us)
    return {
        "num_events": num_events,
        "elapsed_s": round(elapsed, 4),
        "events_per_second": round(num_events / elapsed, 1),
        "rss_mb_growth": round(max(0.0, rss_after - rss_before), 2),
        "latencies_us": {
            "p50": round(percentile(sorted_lats, 0.50), 1),
            "p95": round(percentile(sorted_lats, 0.95), 1),
            "p99": round(percentile(sorted_lats, 0.99), 1),
            "p999": round(percentile(sorted_lats, 0.999), 1),
            "max": round(max(latencies_us) if latencies_us else 0.0, 1),
        },
    }


# ---------------------------------------------------------------------------
# 2. Native Pipeline Benchmark
# ---------------------------------------------------------------------------
def benchmark_native_pipeline(num_events: int = 20_000, seed: int = 42) -> dict[str, Any]:
    """Measure C fastpath quality evaluation if available, or report pure Python fallback."""
    try:
        from mdrap.fastpath import FastQualityEngine, is_fastpath_available

        native_active = is_fastpath_available()
    except ImportError:
        native_active = False

    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))
    events = [raw for raw, _ in sim.generate()]

    t0 = time.perf_counter()
    if native_active:
        fqe = FastQualityEngine()
        for raw in events:
            fqe.evaluate_raw(raw)
    else:
        # Pure Python fallback
        engine = Engine()
        clock = SystemClock()
        st = engine.state
        for raw in events:
            st, _ = engine.step(st, raw, clock)
    t1 = time.perf_counter()

    elapsed = max(0.0001, t1 - t0)
    return {
        "native_accelerated": native_active,
        "num_events": num_events,
        "elapsed_s": round(elapsed, 4),
        "events_per_second": round(num_events / elapsed, 1),
    }


# ---------------------------------------------------------------------------
# 3. IngestLog WAL Benchmark
# ---------------------------------------------------------------------------
def benchmark_ingest_log(num_events: int = 20_000, seed: int = 42) -> dict[str, Any]:
    """Measure durable segmented IngestLog append throughput and recovery replay rate."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_bench_log_")
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))
    events = [raw for raw, _ in sim.generate()]

    # 1. Append throughput (batched fsync policy)
    log = IngestLog(temp_dir, fsync_policy="grouped_by_time")
    t0 = time.perf_counter()
    for raw in events:
        log.append(raw)
    log.flush()
    t1 = time.perf_counter()
    append_s = max(0.0001, t1 - t0)
    log.close()

    # 2. Replay / Recovery throughput
    t_rec_0 = time.perf_counter()
    reopened = IngestLog(temp_dir)
    recovered_count = sum(1 for _ in reopened.iter_from(0))
    t_rec_1 = time.perf_counter()
    recovery_s = max(0.0001, t_rec_1 - t_rec_0)
    reopened.close()

    shutil.rmtree(temp_dir, ignore_errors=True)
    return {
        "num_events": num_events,
        "append_elapsed_s": round(append_s, 4),
        "append_rate_eps": round(num_events / append_s, 1),
        "recovery_elapsed_s": round(recovery_s, 4),
        "recovery_rate_eps": round(recovered_count / recovery_s, 1),
    }


# ---------------------------------------------------------------------------
# 4. SQLite Projection Benchmark
# ---------------------------------------------------------------------------
def benchmark_projection(num_events: int = 20_000, batch_size: int = 500, seed: int = 42) -> dict[str, Any]:
    """Measure SQLite projection atomic batch apply throughput."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_bench_proj_")
    db_path = os.path.join(temp_dir, "projection.db")

    proj = SQLiteProjection(db_path)
    engine = Engine()
    clock = SystemClock()
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))

    decisions = []
    st = engine.state
    for idx, (raw, _) in enumerate(sim.generate()):
        st, dec = engine.step(st, raw, clock, offset=idx)
        decisions.append(dec)

    t0 = time.perf_counter()
    for i in range(0, len(decisions), batch_size):
        chunk = decisions[i : i + batch_size]
        proj.apply(chunk, chunk[-1].offset)
    t1 = time.perf_counter()

    elapsed = max(0.0001, t1 - t0)
    proj.close()
    shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "num_events": num_events,
        "batch_size": batch_size,
        "elapsed_s": round(elapsed, 4),
        "events_per_second": round(num_events / elapsed, 1),
    }


# ---------------------------------------------------------------------------
# 5. End-to-End Engine Pipeline Benchmark
# ---------------------------------------------------------------------------
def benchmark_end_to_end(num_events: int = 20_000, batch_size: int = 500, seed: int = 42) -> dict[str, Any]:
    """Measure end-to-end Engine: IngestLog WAL + deterministic state fold + SQLite projection."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_bench_e2e_")
    wal_dir = os.path.join(temp_dir, "wal")
    db_path = os.path.join(temp_dir, "canonical.db")

    engine = Engine.open(
        wal_dir,
        config=EngineConfig(
            db_path=db_path,
            fsync_policy="grouped_by_time",
        ),
    )
    sim = FeedSimulator(SimulatorConfig(seed=seed, num_events=num_events))

    rss_before = get_rss_mb()
    t0 = time.perf_counter()

    batch = []
    for raw, _ in sim.generate():
        batch.append(raw)
        if len(batch) >= batch_size:
            engine.submit(batch)
            batch.clear()
    if batch:
        engine.submit(batch)

    t1 = time.perf_counter()
    elapsed = max(0.0001, t1 - t0)
    rss_after = get_rss_mb()

    metrics = engine.metrics()
    ticks = engine.query("AAPL", limit=num_events)
    engine.close()

    shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "num_events": num_events,
        "batch_size": batch_size,
        "elapsed_s": round(elapsed, 4),
        "events_per_second": round(num_events / elapsed, 1),
        "rss_mb_growth": round(max(0.0, rss_after - rss_before), 2),
        "persisted_canonical_ticks": len(ticks),
        "log_head_offset": metrics["log_head_offset"],
        "projection_lag": metrics["projection_lag"],
        "health_status": metrics["health_status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MDRAP Phase 6 Benchmark Suite")
    parser.add_argument("--events", type=int, default=10_000, help="Number of events to simulate per test")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for batch tests")
    parser.add_argument("--output", type=str, default="", help="Path to save output JSON")
    args = parser.parse_args()

    meta = get_system_metadata()

    print("=" * 66)
    print("MDRAP Phase 6 Comprehensive Benchmark Suite")
    print(f"Platform: {meta['os']} | CPU: {meta['processor']} ({meta['cpu_count']} cores)")
    print(f"Python: {meta['python_version']} | Commit: {meta['git_commit'][:10]}")
    print("=" * 66)

    print(f"\n1. Measuring Pure Python Pipeline ({args.events} events)...")
    res_py = benchmark_python_pipeline(num_events=args.events)
    print(f"   Throughput: {res_py['events_per_second']:,} ev/s")
    print(f"   p50: {res_py['latencies_us']['p50']} us | p99: {res_py['latencies_us']['p99']} us | p99.9: {res_py['latencies_us']['p999']} us")

    print(f"\n2. Measuring Native / Fastpath Pipeline ({args.events} events)...")
    res_native = benchmark_native_pipeline(num_events=args.events)
    print(f"   Throughput: {res_native['events_per_second']:,} ev/s (accelerated={res_native['native_accelerated']})")

    print(f"\n3. Measuring IngestLog WAL ({args.events} events)...")
    res_wal = benchmark_ingest_log(num_events=args.events)
    print(f"   Append Rate: {res_wal['append_rate_eps']:,} ev/s")
    print(f"   Recovery Rate: {res_wal['recovery_rate_eps']:,} ev/s")

    print(f"\n4. Measuring SQLite Projection ({args.events} events, batch_size={args.batch_size})...")
    res_proj = benchmark_projection(num_events=args.events, batch_size=args.batch_size)
    print(f"   Throughput: {res_proj['events_per_second']:,} ev/s")

    print(f"\n5. Measuring End-to-End Engine Pipeline ({args.events} events)...")
    res_e2e = benchmark_end_to_end(num_events=args.events, batch_size=args.batch_size)
    print(f"   Throughput: {res_e2e['events_per_second']:,} ev/s")
    print(f"   Health: {res_e2e['health_status']} | Projection Lag: {res_e2e['projection_lag']}")

    full_report = {
        "metadata": meta,
        "python_pipeline": res_py,
        "native_pipeline": res_native,
        "ingest_log": res_wal,
        "sqlite_projection": res_proj,
        "end_to_end_engine": res_e2e,
    }

    out_path = args.output
    if not out_path:
        out_dir = ROOT / "benchmarks" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / "phase6_benchmarks.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)

    print(f"\n[OK] Results saved to {out_path}")


if __name__ == "__main__":
    main()
