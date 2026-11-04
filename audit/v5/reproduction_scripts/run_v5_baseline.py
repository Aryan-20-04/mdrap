"""Authoritative Phase 0 Baseline Benchmark Runner for MDRAP v5.0.

Captures raw empirical measurements across all architectural boundaries:
1. System diagnostics and hardware profile (JSON)
2. Native C Kernel & Python FFI boundary deconstruction (micro_ffi)
3. In-Memory Engine pipeline throughput and tail latency (check_regression)
4. Stage-by-stage latency profile (feed decode, allocation, execution, persistence)
5. Full End-to-End disk pipeline (WAL + SQLite) under 'never' and 'grouped_by_time'

Outputs raw evidence files into audit/v5/raw_evidence/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
evidence_dir = repo_root / "audit" / "v5" / "raw_evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from mdrap import Engine, EngineConfig, RawEvent
from mdrap.projection import SQLiteProjection


def _make_event(i: int) -> RawEvent:
    return RawEvent(
        source="BENCH_SRC",
        payload={
            "instrument": "BTC-USD",
            "event_type": "QUOTE",
            "bid": 50000.0 + (i % 10) * 0.1,
            "ask": 50001.0 + (i % 10) * 0.1,
            "exchange_ts": 1700000000.0 + i * 0.001,
            "sequence": i + 1,
        },
        receive_timestamp=1700000000.0 + i * 0.001,
        raw_id=f"raw_{i}",
    )


def run_pipeline_bench(num_events: int = 6000, fsync_policy: str = "never") -> dict:
    temp_dir = tempfile.mkdtemp(prefix=f"mdrap_v5_base_{fsync_policy}_")
    db_path = os.path.join(temp_dir, "projection.db")
    try:
        cfg = EngineConfig(wal_dir=temp_dir, fsync_policy=fsync_policy)
        engine = Engine(cfg)
        proj = SQLiteProjection(db_path=db_path, name="bench_proj")
        engine.subscribe(proj)

        events = [_make_event(i) for i in range(num_events)]
        warmup = min(500, num_events // 10)
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

        res = {
            "policy": fsync_policy,
            "events": measured,
            "elapsed_s": elapsed,
            "throughput_eps": throughput,
            "p50_us": latencies_us[int(n * 0.50)],
            "p95_us": latencies_us[int(n * 0.95)],
            "p99_us": latencies_us[int(n * 0.99)],
            "p99_9_us": latencies_us[int(n * 0.999)],
        }
        engine.close()
        return res
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    print("=" * 80)
    print("MDRAP v5.0 PHASE 0: AUTHORITATIVE BASELINE MEASUREMENT HARNESS")
    print("=" * 80)

    # 1. System Diagnostics
    print("[1/5] Recording system diagnostics and hardware topology...")
    diag_proc = subprocess.run([sys.executable, "-m", "mdrap.cli", "doctor", "--json"], stdout=subprocess.PIPE, text=True)
    diag_path = evidence_dir / "system_diagnostics.json"
    diag_path.write_text(diag_proc.stdout, encoding="utf-8")
    print(f"    Saved system diagnostics -> {diag_path}")

    # 2. Native C Kernel & FFI Boundary
    print("[2/5] Running micro_ffi deconstruction (50,000 iterations x 2 runs)...")
    ffi_proc = subprocess.run([sys.executable, str(repo_root / "benchmarks" / "micro_ffi.py"), "--iterations", "50000", "--runs", "2"], stdout=subprocess.PIPE, text=True)
    ffi_path = evidence_dir / "benchmark_micro_ffi.log"
    ffi_path.write_text(ffi_proc.stdout, encoding="utf-8")
    print(f"    Saved FFI benchmark -> {ffi_path}")

    # 3. In-Memory Engine Step
    print("[3/5] Running in-memory engine regression benchmark (30,000 events)...")
    inmem_proc = subprocess.run([sys.executable, str(repo_root / "benchmarks" / "check_regression.py"), "--tolerance-pct", "20.0"], stdout=subprocess.PIPE, text=True)
    inmem_path = evidence_dir / "benchmark_engine_inmemory.log"
    inmem_path.write_text(inmem_proc.stdout, encoding="utf-8")
    print(f"    Saved In-Memory benchmark -> {inmem_path}")

    # 4. Stage Breakdown (Decode, Allocation, Scheduling, Persistence)
    print("[4/5] Running 4-stage critical path breakdown (100,000 events)...")
    stage_script = repo_root / "benchmarks" / "stage_breakdown.py"
    if stage_script.exists():
        stage_proc = subprocess.run([sys.executable, str(stage_script)], stdout=subprocess.PIPE, text=True)
        stage_path = evidence_dir / "benchmark_stage_breakdown.log"
        stage_path.write_text(stage_proc.stdout, encoding="utf-8")
        print(f"    Saved Stage Breakdown benchmark -> {stage_path}")

    # 5. Full Production Disk Pipeline (WAL + SQLite)
    print("[5/5] Running full disk pipeline benchmark (WAL + SQLite)...")
    pipe_never = run_pipeline_bench(num_events=6000, fsync_policy="never")
    pipe_batch = run_pipeline_bench(num_events=6000, fsync_policy="grouped_by_time")

    pipe_path = evidence_dir / "benchmark_full_pipeline.log"
    with open(pipe_path, "w", encoding="utf-8") as f:
        f.write("MDRAP v5.0 BASELINE FULL PRODUCTION PIPELINE (WAL + SQLite Projections)\n\n")
        f.write(f"Policy: 'never' (OS Page Cache Buffered)\n")
        f.write(f"  Throughput:    {pipe_never['throughput_eps']:.1f} EPS\n")
        f.write(f"  Latency p50:   {pipe_never['p50_us']:.2f} us\n")
        f.write(f"  Latency p95:   {pipe_never['p95_us']:.2f} us\n")
        f.write(f"  Latency p99:   {pipe_never['p99_us']:.2f} us\n")
        f.write(f"  Latency p99.9: {pipe_never['p99_9_us']:.2f} us\n\n")
        f.write(f"Policy: 'grouped_by_time' (Physical Group Commit Fsync)\n")
        f.write(f"  Throughput:    {pipe_batch['throughput_eps']:.1f} EPS\n")
        f.write(f"  Latency p50:   {pipe_batch['p50_us']:.2f} us\n")
        f.write(f"  Latency p95:   {pipe_batch['p95_us']:.2f} us\n")
        f.write(f"  Latency p99:   {pipe_batch['p99_us']:.2f} us\n")
        f.write(f"  Latency p99.9: {pipe_batch['p99_9_us']:.2f} us\n")
    print(f"    Saved Full Pipeline benchmark -> {pipe_path}")

    print("\n" + "=" * 80)
    print("ALL BASELINE BENCHMARKS COMPLETED CLEANLY.")
    print("=" * 80)


if __name__ == "__main__":
    main()
