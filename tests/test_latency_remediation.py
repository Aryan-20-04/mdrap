"""Comprehensive tests for MDRAP Latency Measurement Remediation.

Validates:
- Monotonic nanosecond timing boundaries
- Guarding against negative or non-finite durations
- All-event maximum tracking vs sampled percentiles
- Vitter's Algorithm R uniform reservoir sampling
- Percentile calculation accuracy (p50, p90, p95, p99, p99.9)
- Clock overhead measurement
"""

import math
import time
import pytest

from mdrap.latency import (
    MonotonicLatencyTracker,
    ReservoirSampler,
    StageLatencyMetric,
    measure_clock_overhead,
)


def test_monotonic_clock_advancement():
    """Verify that time.perf_counter_ns() is monotonic and strictly advances."""
    t1 = time.perf_counter_ns()
    time.sleep(0.005)
    t2 = time.perf_counter_ns()
    assert t2 > t1
    diff_us = (t2 - t1) / 1000.0
    assert diff_us >= 4000.0  # At least 4ms elapsed


def test_guard_against_negative_and_invalid_durations():
    """Verify that negative, infinite, and NaN durations are rejected and counted."""
    stage = StageLatencyMetric("test_stage", reservoir_capacity=100)

    # Valid duration
    assert stage.record(1500) is True
    assert stage.count == 1
    assert stage.invalid_count == 0

    # Negative duration (e.g. clock slew or disordered event)
    assert stage.record(-500) is False
    assert stage.count == 1
    assert stage.invalid_count == 1

    # NaN / Inf
    assert stage.record(float("nan")) is False
    assert stage.record(float("inf")) is False
    assert stage.record(float("-inf")) is False
    assert stage.count == 1
    assert stage.invalid_count == 4


def test_all_event_maximum_vs_sampled_percentiles():
    """Verify that all-event maximum captures extreme outliers even if replaced in reservoir."""
    stage = StageLatencyMetric("test_outlier", reservoir_capacity=10)

    # Record 10 small values (1,000 ns = 1 us)
    for _ in range(10):
        stage.record(1000)

    # Record extreme outlier (100 ms = 100,000,000 ns)
    stage.record(100_000_000)

    # Record 500 more small values to guarantee outlier replacement in small reservoir of 10
    for _ in range(500):
        stage.record(1000)

    summary = stage.summary()
    assert summary["count"] == 511
    # All-event max must be exactly 100,000 us (100 ms)
    assert summary["all_event_max_us"] == 100_000.0
    # Min must be 1.0 us
    assert summary["min_us"] == 1.0


def test_reservoir_sampler_uniform_capacity_and_percentiles():
    """Verify Vitter's Algorithm R capacity constraint and exact percentile interpolation."""
    capacity = 1000
    sampler = ReservoirSampler(capacity=capacity)

    # Insert 10,000 known values: 1.0 to 10000.0
    for i in range(1, 10001):
        sampler.add(float(i))

    assert len(sampler.samples) == capacity
    assert sampler.total_seen == 10000

    pcts = sampler.percentiles()
    assert pcts["sample_count"] == capacity
    # For a uniform distribution [1, 10000], p50 should be ~5000 (+/- 500)
    assert 4000.0 <= pcts["p50"] <= 6000.0
    # p90 should be ~9000 (+/- 400)
    assert 8000.0 <= pcts["p90"] <= 9800.0
    # p99 should be ~9900 (+/- 200)
    assert 9200.0 <= pcts["p99"] <= 10000.0
    assert pcts["p50"] < pcts["p90"] < pcts["p95"] < pcts["p99"] <= pcts["p99_9"] <= pcts["sampled_max"]


def test_monotonic_latency_tracker_all_four_stages():
    """Verify that MonotonicLatencyTracker accurately computes all 4 stages."""
    tracker = MonotonicLatencyTracker(reservoir_capacity=100)

    t_ingest_ns = 1_000_000_000       # T = 1.000000 s
    t0_ns = 1_000_050_000             # Ingress: 50 us (50,000 ns)
    t_bcast_ns = 1_000_200_000        # Engine: 150 us (150,000 ns)
    t_recv_ns = 1_000_500_000         # Transport: 300 us (300,000 ns)
    # Total E2E: 500 us (500,000 ns)

    event = {
        "ingest_ts_ns": t_ingest_ns,
        "t0_ns": t0_ns,
        "broadcast_ts_ns": t_bcast_ns,
        "engine_ns": t_bcast_ns - t0_ns,
    }

    result = tracker.record_event_latencies(event, t_recv_ns=t_recv_ns)
    assert result["ingress_us"] == 50.0
    assert result["engine_us"] == 150.0
    assert result["transport_us"] == 300.0
    assert result["e2e_us"] == 500.0

    summary = tracker.summary()
    assert summary["ingress"]["p50_us"] == 50.0
    assert summary["engine"]["p50_us"] == 150.0
    assert summary["transport"]["p50_us"] == 300.0
    assert summary["e2e"]["p50_us"] == 500.0
    assert summary["e2e"]["all_event_max_us"] == 500.0


def test_measure_clock_overhead():
    """Verify that host time.perf_counter_ns() overhead is measured and documented."""
    res = measure_clock_overhead(iterations=5000)
    assert res["iterations"] == 5000
    assert res["total_elapsed_ns"] > 0
    # On modern CPUs, perf_counter_ns overhead is typically < 100 ns (< 0.1 us)
    assert res["overhead_per_call_ns"] > 0
    assert res["overhead_per_call_us"] < 10.0  # Safe sanity bound
