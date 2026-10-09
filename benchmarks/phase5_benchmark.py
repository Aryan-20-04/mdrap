"""MDRAP Phase 5 Production Pilot Benchmark Suite.

Measures:
  1. Pilot Soak Workload (25,000 events): Ingress -> Quality -> WAL -> SBE -> Independent Consumer.
  2. End-to-end pipeline latency distribution (p50, p95, p99, p99.9, max).
  3. Memory allocation delta and peak RSS during pilot execution.
Outputs to audit/phase5/benchmark_results.json.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.ingestlog import IngestLog
from mdrap.models import QualityStatus
from mdrap.quality import QualityEngine


def _compute_percentiles(latencies_ns: List[int]) -> Dict[str, float]:
    if not latencies_ns:
        return {"p50_us": 0.0, "p95_us": 0.0, "p99_us": 0.0, "p999_us": 0.0, "max_us": 0.0}
    latencies_ns.sort()
    n = len(latencies_ns)
    return {
        "p50_us": round(latencies_ns[int(n * 0.50)] / 1000.0, 2),
        "p95_us": round(latencies_ns[int(n * 0.95)] / 1000.0, 2),
        "p99_us": round(latencies_ns[int(n * 0.99)] / 1000.0, 2),
        "p999_us": round(latencies_ns[min(int(n * 0.999), n - 1)] / 1000.0, 2),
        "max_us": round(latencies_ns[-1] / 1000.0, 2),
    }


def benchmark_pilot_pipeline(event_count: int = 25000) -> Dict[str, Any]:
    print(f"[*] Running Pilot Workload Benchmark ({event_count:,} events)...")
    with tempfile.TemporaryDirectory(prefix="mdrap_pilot_bench_") as tmpdir:
        wal_dir = os.path.join(tmpdir, "wal")
        os.makedirs(wal_dir, exist_ok=True)

        frames = [
            {
                "seq": i,
                "sym": "AAPL" if i % 2 == 0 else "MSFT",
                "px": 150.0 + (i % 50) * 0.02,
                "sz": 50.0,
                "type": "TRADE",
            }
            for i in range(1, event_count + 1)
        ]

        adapter = ReplayFeedAdapter(
            FeedAdapterConfig(venue="PILOT_NASDAQ", feed_id="PILOT_CH1"),
            frames=frames,
        )
        adapter.connect()
        quality = QualityEngine()
        ingest_log = IngestLog(
            log_dir=wal_dir,
            fsync_policy="grouped_by_size",
            max_segment_bytes=10 * 1024 * 1024,
        )

        # Independent Consumer Sink
        consumed_events = 0
        latencies_ns: List[int] = []

        tracemalloc.start()
        mem_before_current, _ = tracemalloc.get_traced_memory()

        t0 = time.perf_counter()

        for _ in range(event_count):
            s0 = time.perf_counter_ns()
            raw = adapter.poll()
            if raw is None:
                break

            canon = adapter.normalize(raw)
            eval_res = quality.evaluate(canon)

            # Persist to WAL
            _ = ingest_log.append(raw)

            # SBE frame pack
            inst_b = canon.instrument_id.encode("ascii").ljust(16, b"\x00")
            sbe = struct.pack(
                "<16sQqddII8s",
                inst_b,
                canon.sequence_number or 0,
                int(canon.exchange_timestamp * 1e9),
                canon.price or 0.0,
                canon.quantity or 0.0,
                1,
                0 if eval_res.quality_status == QualityStatus.VALID else 1,
                b"\x00" * 8,
            )

            # Independent Consumer unpack
            u_inst, u_seq, u_ts, u_px, u_sz, _, _, _ = struct.unpack("<16sQqddII8s", sbe)
            consumed_events += 1

            s1 = time.perf_counter_ns()
            latencies_ns.append(s1 - s0)

        total_time_s = time.perf_counter() - t0
        mem_after_current, mem_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        ingest_log.flush()
        ingest_log.close()
        adapter.disconnect()

        rate = round(event_count / total_time_s, 1)
        pct = _compute_percentiles(latencies_ns)

        return {
            "pilot_profile": "Profile A (Single-Node High Throughput)",
            "event_count": event_count,
            "events_consumed": consumed_events,
            "elapsed_s": round(total_time_s, 4),
            "throughput_eps": rate,
            "mem_delta_mb": round((mem_after_current - mem_before_current) / (1024 * 1024), 3),
            "mem_peak_mb": round(mem_peak / (1024 * 1024), 3),
            **pct,
        }


def run_phase5_benchmarks() -> Dict[str, Any]:
    print("=== MDRAP Phase 5 Pilot Benchmark Suite ===")
    results = {
        "timestamp": time.time(),
        "pilot_run": benchmark_pilot_pipeline(25000),
    }

    out_path = os.path.join("audit", "phase5", "benchmark_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n[+] Phase 5 Benchmark completed. Output written to {out_path}")
    return results


if __name__ == "__main__":
    run_phase5_benchmarks()
