"""Experiment 1: CME Packet Trace Burst Replay & Queueing (Strategy §14.5 / Maciejewski 2026).

Implements CME MDP 3.0 / E-mini NQ burst replay and compares single-thread,
2-stage pipelined, and sharded-worker queueing topologies under microsecond
market arrival bursts.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from mdrap.models import RawEvent

try:
    from benchmarks.experiments.exp1_burst_queueing import (
        TopologyResult,
        generate_burst_workload,
        run_experiment_1,
        run_sharded_workers,
        run_single_thread,
        run_two_stage_pipeline,
    )
except ImportError:
    from exp1_burst_queueing import (  # type: ignore
        TopologyResult,
        generate_burst_workload,
        run_experiment_1,
        run_sharded_workers,
        run_single_thread,
        run_two_stage_pipeline,
    )

__all__ = [
    "TopologyResult",
    "generate_burst_workload",
    "generate_cme_burst_trace",
    "run_cme_burst_experiment",
    "run_single_thread",
    "run_two_stage_pipeline",
    "run_sharded_workers",
    "run_experiment_1",
]


def generate_cme_burst_trace(
    total_packets: int = 5_000,
    burst_intensity: float = 0.30,
    seed: int = 42,
) -> list[RawEvent]:
    """Generate synthetic CME MDP 3.0 SBE packet arrival trace modeled after CME NQ market open."""
    rng = random.Random(seed)
    instruments = ["NQ_FUT", "ES_FUT", "RTY_FUT", "YM_FUT"]
    base_prices = {
        "NQ_FUT": 20250.25,
        "ES_FUT": 5850.50,
        "RTY_FUT": 2240.10,
        "YM_FUT": 42800.0,
    }
    sequences = {inst: 1 for inst in instruments}
    trace: list[RawEvent] = []

    now = time.time()
    t = now
    for i in range(total_packets):
        is_burst = rng.random() < burst_intensity
        # Burst: packets arrive within 1-5 microseconds; Quiescent: 100-300 microseconds
        dt = (
            rng.uniform(0.000001, 0.000005) if is_burst else rng.uniform(0.0001, 0.0003)
        )
        t += dt

        inst = rng.choice(instruments)
        price_delta = rng.uniform(-0.50, 0.50)
        seq = sequences[inst]
        sequences[inst] += 1

        payload = {
            "instrument": inst,
            "event_type": "TRADE" if (i % 3 != 0) else "QUOTE",
            "price": round(base_prices[inst] + price_delta, 2),
            "quantity": round(rng.uniform(1.0, 20.0), 1),
            "bid": round(base_prices[inst] + price_delta - 0.25, 2),
            "ask": round(base_prices[inst] + price_delta + 0.25, 2),
            "exchange_ts": t,
            "sequence": seq,
            "channel_id": f"CME_CH_{hash(inst) % 8}",
        }
        trace.append(RawEvent(source="CME_MDP3", payload=payload, receive_timestamp=t))

    return trace


def run_cme_burst_experiment(total_packets: int = 5_000) -> dict[str, Any]:
    """Execute CME burst replay across topologies and verify sequence preservation."""
    packets = generate_cme_burst_trace(total_packets=total_packets, seed=42)
    res_single = run_single_thread(packets)
    res_pipelined = run_two_stage_pipeline(packets)
    res_sharded = run_sharded_workers(packets, num_shards=4)

    return {
        "total_packets": total_packets,
        "single_thread": res_single.to_dict(),
        "pipelined": res_pipelined.to_dict(),
        "sharded": res_sharded.to_dict(),
        "sequence_integrity": (
            res_single.sequence_gaps == 0
            and res_pipelined.sequence_gaps == 0
            and res_sharded.sequence_gaps == 0
        ),
    }


if __name__ == "__main__":
    print("=" * 70)
    print("MDRAP Experiment 1: CME Packet Trace Burst Replay & Topology Benchmark")
    print("=" * 70)
    results = run_cme_burst_experiment(total_packets=10_000)
    for top in ("single_thread", "pipelined", "sharded"):
        d = results[top]
        print(
            f"Topology: {d['topology']:22s} | Throughput: {d['throughput_eps']:9.1f} eps | "
            f"p50: {d['p50_us']:5.1f} us | p99: {d['p99_us']:5.1f} us | max_q: {d['max_queue_depth']}"
        )
    print(f"Sequence Integrity: {results['sequence_integrity']}")
