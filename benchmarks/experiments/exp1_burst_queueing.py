"""Experiment 1: Burst-Shape Replay and Queueing Topology (Strategy §14.5 / Maciejewski 2026).

Compares single-threaded vs 2-stage pipelined vs sharded-worker execution topologies
under synthetic clustered burst workloads modeled after exchange market-data arrivals.
"""

from __future__ import annotations

import collections
import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from mdrap.clock import SystemClock
from mdrap.engine import Engine, EngineState
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent

__stability__ = "experimental"


@dataclass
class TopologyResult:
    topology_name: str
    total_events: int
    elapsed_seconds: float
    throughput_events_per_sec: float
    latencies_us: list[float]
    p50_us: float
    p90_us: float
    p99_us: float
    p99_9_us: float
    max_us: float
    max_queue_depth: int
    dropped_events: int
    sequence_gaps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "topology": self.topology_name,
            "total_events": self.total_events,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
            "throughput_eps": round(self.throughput_events_per_sec, 2),
            "p50_us": round(self.p50_us, 2),
            "p90_us": round(self.p90_us, 2),
            "p99_us": round(self.p99_us, 2),
            "p99_9_us": round(self.p99_9_us, 2),
            "max_us": round(self.max_us, 2),
            "max_queue_depth": self.max_queue_depth,
            "dropped_events": self.dropped_events,
            "sequence_gaps": self.sequence_gaps,
        }


def _calc_percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    idx = int(k)
    return sorted_vals[idx]


def generate_burst_workload(
    total_events: int = 10_000,
    burst_factor: float = 0.25,
    seed: int = 42,
) -> list[RawEvent]:
    """Generate a sequence of market events with periodic clustered bursts."""
    rng = random.Random(seed)
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AAPL", "NQ_FUT"]
    events = []
    base_price = {
        "BTC/USDT": 65000.0,
        "ETH/USDT": 3500.0,
        "SOL/USDT": 150.0,
        "AAPL": 220.0,
        "NQ_FUT": 20000.0,
    }
    seq_counters = {s: 1 for s in symbols}

    now = time.time()
    t = now
    for i in range(total_events):
        # Clustered arrival: 25% of events arrive in tight microsecond bursts
        is_burst = rng.random() < burst_factor
        dt = rng.uniform(0.000001, 0.00001) if is_burst else rng.uniform(0.0001, 0.0005)
        t += dt

        sym = rng.choice(symbols)
        price = base_price[sym] + rng.uniform(-1.0, 1.0)
        seq = seq_counters[sym]
        seq_counters[sym] += 1

        payload = {
            "instrument": sym,
            "event_type": "TRADE",
            "price": round(price, 4),
            "quantity": round(rng.uniform(0.01, 2.5), 4),
            "exchange_ts": t,
            "sequence": seq,
        }
        events.append(
            RawEvent(source="BURST_SIM", payload=payload, receive_timestamp=t)
        )
    return events


def run_single_thread(events: list[RawEvent]) -> TopologyResult:
    """Topology A: Single-threaded event loop (inline validation and state fold)."""
    engine = Engine()
    state = EngineState()
    clock = SystemClock()
    latencies: list[float] = []

    t0 = time.perf_counter()
    for raw in events:
        start_ns = time.perf_counter_ns()
        state, decision = engine.step(state, raw, clock)
        latencies.append((time.perf_counter_ns() - start_ns) / 1000.0)
    elapsed = max(time.perf_counter() - t0, 1e-6)

    sorted_lats = sorted(latencies)
    return TopologyResult(
        topology_name="SingleThreadLoop",
        total_events=len(events),
        elapsed_seconds=elapsed,
        throughput_events_per_sec=len(events) / elapsed,
        latencies_us=latencies,
        p50_us=_calc_percentile(sorted_lats, 50.0),
        p90_us=_calc_percentile(sorted_lats, 90.0),
        p99_us=_calc_percentile(sorted_lats, 99.0),
        p99_9_us=_calc_percentile(sorted_lats, 99.9),
        max_us=sorted_lats[-1] if sorted_lats else 0.0,
        max_queue_depth=0,
        dropped_events=0,
        sequence_gaps=0,
    )


def run_two_stage_pipeline(
    events: list[RawEvent], max_q_size: int = 50_000
) -> TopologyResult:
    """Topology B: Two-stage pipelined execution (Worker queue handoff)."""
    q: queue.Queue = queue.Queue(maxsize=max_q_size)
    engine = Engine()
    state = EngineState()
    clock = SystemClock()
    latencies: list[float] = []
    max_depth = 0
    done = threading.Event()

    def worker():
        nonlocal state
        while True:
            item = q.get()
            if item is None:
                q.task_done()
                break
            raw, start_ns = item
            state, decision = engine.step(state, raw, clock)
            latencies.append((time.perf_counter_ns() - start_ns) / 1000.0)
            q.task_done()

    t_worker = threading.Thread(target=worker, daemon=True)
    t_worker.start()

    t0 = time.perf_counter()
    for raw in events:
        cur_depth = q.qsize()
        if cur_depth > max_depth:
            max_depth = cur_depth
        start_ns = time.perf_counter_ns()
        q.put((raw, start_ns))

    q.put(None)
    t_worker.join(timeout=10.0)
    elapsed = max(time.perf_counter() - t0, 1e-6)

    sorted_lats = sorted(latencies)
    return TopologyResult(
        topology_name="TwoStagePipeline",
        total_events=len(events),
        elapsed_seconds=elapsed,
        throughput_events_per_sec=len(events) / elapsed,
        latencies_us=latencies,
        p50_us=_calc_percentile(sorted_lats, 50.0),
        p90_us=_calc_percentile(sorted_lats, 90.0),
        p99_us=_calc_percentile(sorted_lats, 99.0),
        p99_9_us=_calc_percentile(sorted_lats, 99.9),
        max_us=sorted_lats[-1] if sorted_lats else 0.0,
        max_queue_depth=max_depth,
        dropped_events=0,
        sequence_gaps=0,
    )


def run_sharded_workers(events: list[RawEvent], num_shards: int = 4) -> TopologyResult:
    """Topology C: Sharded per-instrument workers preserving per-symbol sequence ordering."""
    queues: list[queue.Queue] = [queue.Queue(maxsize=20_000) for _ in range(num_shards)]
    latencies: list[float] = []
    lat_lock = threading.Lock()
    max_depth = 0
    engines = [Engine() for _ in range(num_shards)]
    states = [EngineState() for _ in range(num_shards)]
    clocks = [SystemClock() for _ in range(num_shards)]

    def shard_worker(shard_idx: int):
        eng = engines[shard_idx]
        st = states[shard_idx]
        clk = clocks[shard_idx]
        q = queues[shard_idx]
        local_lats = []
        while True:
            item = q.get()
            if item is None:
                q.task_done()
                break
            raw, start_ns = item
            st, _ = eng.step(st, raw, clk)
            local_lats.append((time.perf_counter_ns() - start_ns) / 1000.0)
            q.task_done()
        with lat_lock:
            latencies.extend(local_lats)

    threads = [
        threading.Thread(target=shard_worker, args=(i,), daemon=True)
        for i in range(num_shards)
    ]
    for th in threads:
        th.start()

    t0 = time.perf_counter()
    for raw in events:
        sym = raw.payload.get("instrument", "")
        shard_idx = hash(sym) % num_shards
        q = queues[shard_idx]
        d = q.qsize()
        if d > max_depth:
            max_depth = d
        start_ns = time.perf_counter_ns()
        q.put((raw, start_ns))

    for q in queues:
        q.put(None)
    for th in threads:
        th.join(timeout=10.0)
    elapsed = max(time.perf_counter() - t0, 1e-6)

    sorted_lats = sorted(latencies)
    return TopologyResult(
        topology_name=f"ShardedWorkers({num_shards})",
        total_events=len(events),
        elapsed_seconds=elapsed,
        throughput_events_per_sec=len(events) / elapsed,
        latencies_us=latencies,
        p50_us=_calc_percentile(sorted_lats, 50.0),
        p90_us=_calc_percentile(sorted_lats, 90.0),
        p99_us=_calc_percentile(sorted_lats, 99.0),
        p99_9_us=_calc_percentile(sorted_lats, 99.9),
        max_us=sorted_lats[-1] if sorted_lats else 0.0,
        max_queue_depth=max_depth,
        dropped_events=0,
        sequence_gaps=0,
    )


def run_experiment_1(total_events: int = 5_000) -> list[TopologyResult]:
    """Execute Experiment 1 comparing all 3 execution topologies."""
    events = generate_burst_workload(total_events=total_events, seed=42)
    res_a = run_single_thread(events)
    res_b = run_two_stage_pipeline(events)
    res_c = run_sharded_workers(events, num_shards=4)
    return [res_a, res_b, res_c]


if __name__ == "__main__":
    print("=" * 70)
    print("MDRAP Experiment 1: Burst Replay & Queueing Topology Comparison")
    print("=" * 70)
    results = run_experiment_1(total_events=10_000)
    for r in results:
        d = r.to_dict()
        print(
            f"Topology: {d['topology']:22s} | Throughput: {d['throughput_eps']:9.1f} eps | p50: {d['p50_us']:5.1f} us | p99: {d['p99_us']:5.1f} us | max_q: {d['max_queue_depth']}"
        )
