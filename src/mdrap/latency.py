"""MDRAP Nanosecond Monotonic Latency Telemetry & Statistical Sampling.

Provides high-resolution, jitter-free duration measurements across all market data
pipeline boundaries using time.perf_counter_ns().
"""

from __future__ import annotations

__stability__ = "stable"

import math
import random
import time
from typing import Any, Dict, List, Optional


class ReservoirSampler:
    """Fixed-capacity uniform reservoir sampler implementing Vitter's Algorithm R (1985).

    Guarantees that each observed sample has an identical selection probability K/N
    in O(1) space, avoiding unbounded heap growth during sustained multi-hour workloads.
    """

    def __init__(self, capacity: int = 50000) -> None:
        if capacity <= 0:
            raise ValueError(f"Capacity must be positive, got {capacity}")
        self.capacity: int = capacity
        self.samples: List[float] = []
        self.total_seen: int = 0

    def add(self, value: float) -> None:
        """Add a value to the reservoir using uniform random replacement."""
        self.total_seen += 1
        if len(self.samples) < self.capacity:
            self.samples.append(value)
        else:
            j = random.randint(0, self.total_seen - 1)
            if j < self.capacity:
                self.samples[j] = value

    def percentiles(self) -> Dict[str, float]:
        """Compute statistical percentiles (p50, p90, p95, p99, p99.9, sampled_max)."""
        if not self.samples:
            return {
                "p50": 0.0,
                "p90": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "p99_9": 0.0,
                "sampled_max": 0.0,
                "sample_count": 0,
            }
        s = sorted(self.samples)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 2),
            "p90": round(s[int(n * 0.90)], 2),
            "p95": round(s[int(n * 0.95)], 2),
            "p99": round(s[int(n * 0.99)], 2),
            "p99_9": round(s[min(n - 1, int(n * 0.999))], 2),
            "sampled_max": round(s[-1], 2),
            "sample_count": n,
        }

    def clear(self) -> None:
        self.samples.clear()
        self.total_seen = 0


class StageLatencyMetric:
    """Tracks latency for a single pipeline boundary stage.

    Distinguishes all-event maximum from sampled reservoir percentiles.
    """

    def __init__(self, name: str, reservoir_capacity: int = 50000) -> None:
        self.name: str = name
        self.count: int = 0
        self.sum_ns: int = 0
        self.all_event_min_ns: Optional[int] = None
        self.all_event_max_ns: int = 0
        self.invalid_count: int = 0
        self.reservoir: ReservoirSampler = ReservoirSampler(capacity=reservoir_capacity)

    def record(self, duration_ns: int | float) -> bool:
        """Record a single duration in nanoseconds.

        Guards against negative or non-finite values (e.g. clock jumps).
        Returns True if recorded, False if rejected as invalid.
        """
        if not math.isfinite(duration_ns) or duration_ns < 0:
            self.invalid_count += 1
            return False

        dur_int = int(duration_ns)
        self.count += 1
        self.sum_ns += dur_int

        if self.all_event_min_ns is None or dur_int < self.all_event_min_ns:
            self.all_event_min_ns = dur_int

        if dur_int > self.all_event_max_ns:
            self.all_event_max_ns = dur_int

        # Add duration in microseconds to reservoir for percentile calculations
        self.reservoir.add(dur_int / 1000.0)
        return True

    def summary(self) -> Dict[str, Any]:
        """Return comprehensive latency statistics in microseconds."""
        pcts = self.reservoir.percentiles()
        mean_us = (self.sum_ns / self.count / 1000.0) if self.count > 0 else 0.0
        min_us = (
            (self.all_event_min_ns / 1000.0)
            if self.all_event_min_ns is not None
            else 0.0
        )
        max_us = self.all_event_max_ns / 1000.0

        return {
            "stage": self.name,
            "count": self.count,
            "invalid_count": self.invalid_count,
            "mean_us": round(mean_us, 2),
            "min_us": round(min_us, 2),
            "p50_us": pcts["p50"],
            "p90_us": pcts["p90"],
            "p95_us": pcts["p95"],
            "p99_us": pcts["p99"],
            "p99_9_us": pcts["p99_9"],
            "sampled_max_us": pcts["sampled_max"],
            "all_event_max_us": round(max_us, 2),
        }


class MonotonicLatencyTracker:
    """Comprehensive latency manager covering all four pipeline boundaries.

    Boundaries:
        1. Ingress (Producer -> Daemon start)
        2. Engine (Daemon start -> Normalization complete)
        3. Transport (Daemon broadcast -> Observer receive)
        4. End-to-End (Producer injection -> Observer receive)
    """

    def __init__(self, reservoir_capacity: int = 50000) -> None:
        self.ingress: StageLatencyMetric = StageLatencyMetric(
            "ingress", reservoir_capacity
        )
        self.engine: StageLatencyMetric = StageLatencyMetric(
            "engine", reservoir_capacity
        )
        self.transport: StageLatencyMetric = StageLatencyMetric(
            "transport", reservoir_capacity
        )
        self.e2e: StageLatencyMetric = StageLatencyMetric("e2e", reservoir_capacity)

    def record_event_latencies(
        self,
        event: dict[str, Any],
        t_recv_ns: Optional[int] = None,
    ) -> Dict[str, Optional[float]]:
        """Record all four latency stages from an incoming event dictionary.

        Args:
            event: Event dictionary containing monotonic timestamp fields:
                   - ingest_ts_ns: Producer injection timestamp
                   - t0_ns: Daemon ingest timestamp
                   - broadcast_ts_ns: Daemon broadcast timestamp
                   - engine_ns: Daemon normalization duration
            t_recv_ns: Observer receive monotonic timestamp (defaults to time.perf_counter_ns())

        Returns:
            Dictionary of calculated latencies in microseconds for this event.
        """
        now_ns = t_recv_ns if t_recv_ns is not None else time.perf_counter_ns()
        result: Dict[str, Optional[float]] = {
            "ingress_us": None,
            "engine_us": None,
            "transport_us": None,
            "e2e_us": None,
        }

        t_ingest_ns = event.get("ingest_ts_ns") or 0
        t0_ns = event.get("t0_ns") or 0
        t_bcast_ns = event.get("broadcast_ts_ns") or 0
        engine_dur_ns = event.get("engine_ns")

        # 1. Producer-to-Daemon Ingress Latency
        if t_ingest_ns > 0 and t0_ns > 0:
            ing_ns = t0_ns - t_ingest_ns
            if self.ingress.record(ing_ns):
                result["ingress_us"] = round(ing_ns / 1000.0, 2)

        # 2. Daemon Engine Processing Latency
        if engine_dur_ns is not None:
            if self.engine.record(engine_dur_ns):
                result["engine_us"] = round(engine_dur_ns / 1000.0, 2)
        elif t0_ns > 0 and t_bcast_ns > 0:
            eng_ns = t_bcast_ns - t0_ns
            if self.engine.record(eng_ns):
                result["engine_us"] = round(eng_ns / 1000.0, 2)

        # 3. Daemon-to-Observer Transport Latency
        if t_bcast_ns > 0:
            trans_ns = now_ns - t_bcast_ns
            if self.transport.record(trans_ns):
                result["transport_us"] = round(trans_ns / 1000.0, 2)

        # 4. Complete Producer-to-Observer End-to-End Latency
        if t_ingest_ns > 0:
            e2e_ns = now_ns - t_ingest_ns
            if self.e2e.record(e2e_ns):
                result["e2e_us"] = round(e2e_ns / 1000.0, 2)

        return result

    def summary(self) -> Dict[str, Any]:
        """Return full summary across all four latency stages."""
        return {
            "ingress": self.ingress.summary(),
            "engine": self.engine.summary(),
            "transport": self.transport.summary(),
            "e2e": self.e2e.summary(),
        }


def measure_clock_overhead(iterations: int = 10000) -> Dict[str, float]:
    """Measure the invocation overhead of time.perf_counter_ns() on the host system."""
    t_start = time.perf_counter_ns()
    for _ in range(iterations):
        _ = time.perf_counter_ns()
    t_end = time.perf_counter_ns()
    total_ns = t_end - t_start
    per_call_ns = total_ns / max(1, iterations)
    return {
        "iterations": iterations,
        "total_elapsed_ns": total_ns,
        "overhead_per_call_ns": round(per_call_ns, 2),
        "overhead_per_call_us": round(per_call_ns / 1000.0, 4),
    }
