"""MDRAP Phase 2.5 — Adversarial Latency Tracker Tests.

Independent adversarial verification of MonotonicLatencyTracker, StageLatencyMetric,
and ReservoirSampler under boundary, stress, and corruption conditions.
"""

import math
import random
import time
import pytest

from mdrap.latency import (
    MonotonicLatencyTracker,
    StageLatencyMetric,
    ReservoirSampler,
    measure_clock_overhead,
)


def test_repeated_timestamps_zero_duration():
    """Verify that zero duration (identical monotonic timestamps) is recorded as valid 0 ns."""
    metric = StageLatencyMetric("zero_test")
    assert metric.record(0) is True
    assert metric.count == 1
    assert metric.sum_ns == 0
    assert metric.all_event_min_ns == 0
    assert metric.all_event_max_ns == 0
    assert metric.invalid_count == 0

    summ = metric.summary()
    assert summ["count"] == 1
    assert summ["min_us"] == 0.0
    assert summ["mean_us"] == 0.0
    assert summ["all_event_max_us"] == 0.0


def test_very_small_positive_durations():
    """Verify sub-microsecond durations (1 ns) are recorded accurately in integer nanoseconds."""
    metric = StageLatencyMetric("small_test")
    assert metric.record(1) is True
    assert metric.all_event_min_ns == 1
    assert metric.all_event_max_ns == 1
    assert metric.sum_ns == 1
    # Microsecond representation is rounded to 2 decimals
    summ = metric.summary()
    assert summ["min_us"] == 0.0  # 1 ns / 1000 = 0.001 us -> rounded to 0.00 us
    assert summ["count"] == 1


def test_negative_durations_rejected():
    """Verify that negative durations are rejected, counted in invalid_count, and excluded from statistics."""
    metric = StageLatencyMetric("neg_test")
    assert metric.record(-100) is False
    assert metric.record(-1) is False
    assert metric.invalid_count == 2
    assert metric.count == 0
    assert metric.all_event_min_ns is None
    assert metric.all_event_max_ns == 0

    summ = metric.summary()
    assert summ["count"] == 0
    assert summ["invalid_count"] == 2
    assert summ["p50_us"] == 0.0


def test_invalid_non_finite_timestamps():
    """Verify NaN and infinities are rejected and increment invalid_count."""
    metric = StageLatencyMetric("nan_test")
    assert metric.record(float("nan")) is False
    assert metric.record(float("inf")) is False
    assert metric.record(float("-inf")) is False
    assert metric.invalid_count == 3
    assert metric.count == 0


def test_extremely_large_durations():
    """Verify extremely large durations (e.g. 10^15 ns ~ 11.5 days) do not overflow Python 64-bit int."""
    metric = StageLatencyMetric("huge_test")
    huge_dur = 10**15
    assert metric.record(huge_dur) is True
    assert metric.count == 1
    assert metric.sum_ns == huge_dur
    assert metric.all_event_max_ns == huge_dur
    summ = metric.summary()
    assert summ["all_event_max_us"] == 10**12


def test_empty_sample_populations():
    """Verify empty metric returns clean zeroed percentiles without division by zero."""
    metric = StageLatencyMetric("empty_test")
    summ = metric.summary()
    assert summ["count"] == 0
    assert summ["invalid_count"] == 0
    assert summ["mean_us"] == 0.0
    assert summ["p50_us"] == 0.0
    assert summ["p99_us"] == 0.0
    assert summ["sampled_max_us"] == 0.0
    assert summ["all_event_max_us"] == 0.0


def test_small_sample_populations_under_capacity():
    """Verify accurate percentile computation when N < K."""
    sampler = ReservoirSampler(capacity=50000)
    for i in range(1, 101):  # 1 to 100
        sampler.add(float(i))
    pcts = sampler.percentiles()
    assert pcts["sample_count"] == 100
    assert pcts["p50"] == 51.0
    assert pcts["p90"] == 91.0
    assert pcts["p95"] == 96.0
    assert pcts["p99"] == 100.0
    assert pcts["sampled_max"] == 100.0


def test_exactly_k_samples():
    """Verify reservoir behavior when sample size equals capacity exactly (N = K = 50,000)."""
    sampler = ReservoirSampler(capacity=1000)
    for i in range(1000):
        sampler.add(float(i))
    assert len(sampler.samples) == 1000
    assert sampler.total_seen == 1000
    # Every element from 0 to 999 must be present exactly once
    assert sorted(sampler.samples) == [float(i) for i in range(1000)]


def test_more_than_k_samples_bounded_capacity():
    """Verify reservoir capacity strictly caps at K even with 100,000 samples."""
    sampler = ReservoirSampler(capacity=1000)
    for i in range(100000):
        sampler.add(float(i))
    assert len(sampler.samples) == 1000
    assert sampler.total_seen == 100000


def test_injected_extreme_outliers_early_middle_late():
    """Verify all_event_max_ns captures extreme outliers whether injected early, mid, or late."""
    for position in ["early", "middle", "late"]:
        metric = StageLatencyMetric(f"outlier_{position}", reservoir_capacity=500)
        total_events = 5000
        outlier_val = 500_000_000  # 500 ms = 500,000,000 ns

        for i in range(total_events):
            if position == "early" and i == 5:
                metric.record(outlier_val)
            elif position == "middle" and i == 2500:
                metric.record(outlier_val)
            elif position == "late" and i == 4995:
                metric.record(outlier_val)
            else:
                metric.record(10_000)  # 10 us baseline

        summ = metric.summary()
        # all_event_max_us MUST capture the 500,000 us outlier unconditionally
        assert summ["all_event_max_us"] == 500000.0
        assert metric.all_event_max_ns == outlier_val


def test_reservoir_reproducibility_under_controlled_seed():
    """Verify deterministic reservoir sampling when random seed is controlled."""

    def run_reservoir(seed_val):
        random.seed(seed_val)
        sampler = ReservoirSampler(capacity=100)
        for i in range(1000):
            sampler.add(float(i))
        return list(sampler.samples)

    run1 = run_reservoir(42)
    run2 = run_reservoir(42)
    run3 = run_reservoir(999)

    assert run1 == run2  # Exact deterministic reproduction
    assert run1 != run3  # Different seed yields different random sample subset


def test_multiple_independent_stage_trackers():
    """Verify that multiple trackers do not share mutable state."""
    t1 = MonotonicLatencyTracker(reservoir_capacity=100)
    t2 = MonotonicLatencyTracker(reservoir_capacity=100)

    t1.ingress.record(5000)
    assert t1.ingress.count == 1
    assert t2.ingress.count == 0


def test_e2e_telescoping_identity_and_accounting():
    """Verify exact algebraic telescoping identity:
    (t0 - t_ingest) + (t_bcast - t0) + (t_recv - t_bcast) == (t_recv - t_ingest)
    """
    tracker = MonotonicLatencyTracker()
    t_ingest_ns = 1_000_000_000
    t0_ns = 1_000_010_000  # ingress: 10,000 ns
    t_bcast_ns = 1_000_035_000  # engine: 25,000 ns
    t_recv_ns = 1_000_050_000  # transport: 15,000 ns
    engine_ns = t_bcast_ns - t0_ns

    event = {
        "ingest_ts_ns": t_ingest_ns,
        "t0_ns": t0_ns,
        "broadcast_ts_ns": t_bcast_ns,
        "engine_ns": engine_ns,
    }

    res = tracker.record_event_latencies(event, t_recv_ns=t_recv_ns)
    assert res["ingress_us"] == 10.0
    assert res["engine_us"] == 25.0
    assert res["transport_us"] == 15.0
    assert res["e2e_us"] == 50.0

    # Telescoping check: 10 + 25 + 15 == 50
    assert (
        round(res["ingress_us"] + res["engine_us"] + res["transport_us"], 2)
        == res["e2e_us"]
    )


def test_record_event_latencies_omits_unrecorded_when_timestamps_invert():
    """Verify that record_event_latencies gracefully omits stages when timestamps invert
    (e.g. t0_ns < ingest_ts_ns), returning None without crashing.
    """
    tracker = MonotonicLatencyTracker()
    event = {
        "ingest_ts_ns": 2_000_000,
        "t0_ns": 1_000_000,  # INVERTED: t0 < ingest
        "broadcast_ts_ns": 3_000_000,
        "engine_ns": None,
    }
    res = tracker.record_event_latencies(event, t_recv_ns=4_000_000)
    assert res["ingress_us"] is None  # Ingress skipped because t0 < ingest
    assert res["engine_us"] == 2000.0  # engine: 3M - 1M = 2M ns = 2000 us
    assert res["transport_us"] == 1000.0
    assert res["e2e_us"] == 2000.0
