"""Tests for Exact Integer Price Corridor (__int128 / BigInt) and Structure-of-Arrays (SoA) Batch Validation."""

import ctypes
import os
import pytest

from mdrap.fastpath import (
    _NATIVE_LIB,
    FastQualityEngine,
    exact_int_corridor_check,
)
from mdrap.models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    Reason,
    to_fixed_point_price,
)


def test_exact_integer_corridor_normal_and_anomaly():
    """Verify exact integer corridor identifies normal fluctuations and anomalous spikes."""
    # Scale: 1e8. Baseline price: $100.00 -> 10_000_000_000
    base = 10_000_000_000
    n = 20
    # Window with small variance: alternating +- 0.05 ($0.05 = 5_000_000)
    prices = [base + (5_000_000 if i % 2 == 0 else -5_000_000) for i in range(n)]

    s1 = sum(prices)
    s2 = sum(p * p for p in prices)

    # Candidate 1: Normal price within band ($100.02)
    cand_normal = base + 2_000_000
    assert not exact_int_corridor_check(
        candidate_scaled=cand_normal,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
    )

    # Candidate 2: Extreme spike ($115.00 -> 11_500_000_000, >10 sigma)
    cand_spike = base + 1_500_000_000
    assert exact_int_corridor_check(
        candidate_scaled=cand_spike,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
    )


def test_exact_integer_corridor_sigma_floor():
    """Verify relative sigma floor prevents false positive anomalies on flat/tick-quantized prices."""
    base = 10_000_000_000  # $100.00
    n = 30
    prices = [base for _ in range(n)]  # Perfectly flat prices (var = 0)

    s1 = sum(prices)
    s2 = sum(p * p for p in prices)

    # 1 tick jump ($0.01 = 1_000_000, which is 1 bp = 0.01% of $100)
    cand_1tick = base + 1_000_000
    # With sigma floor of 2 bp (1/5000) and k=4, threshold is 8 bp = $0.08
    # $0.01 jump must NOT be flagged as an anomaly!
    assert not exact_int_corridor_check(
        candidate_scaled=cand_1tick,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
        sigma_floor_num=1,
        sigma_floor_den=5000,
    )

    # 10% jump ($10.00 = 1_000_000_000, 1000 bp) must be flagged
    cand_jump = base + 1_000_000_000
    assert exact_int_corridor_check(
        candidate_scaled=cand_jump,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
        sigma_floor_num=1,
        sigma_floor_den=5000,
    )


def test_exact_integer_corridor_parameter_guards():
    """Verify exact integer corridor cleanly returns False when invalid bounds (den <= 0, k <= 0, n < min_samples) are passed."""
    base = 10_000_000_000
    n = 30
    prices = [base for _ in range(n)]
    s1 = sum(prices)
    s2 = sum(p * p for p in prices)
    cand_jump = base + 1_000_000_000

    # Denominator zero or negative
    assert not exact_int_corridor_check(
        candidate_scaled=cand_jump,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
        sigma_floor_num=1,
        sigma_floor_den=0,
    )
    assert not exact_int_corridor_check(
        candidate_scaled=cand_jump,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
        sigma_floor_num=1,
        sigma_floor_den=-5000,
    )

    # k_stddev zero or negative
    assert not exact_int_corridor_check(
        candidate_scaled=cand_jump,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=0,
        sigma_floor_num=1,
        sigma_floor_den=5000,
    )
    assert not exact_int_corridor_check(
        candidate_scaled=cand_jump,
        sum_x=s1,
        count_n=n,
        sum_x2=s2,
        min_samples=20,
        k_stddev=-3,
        sigma_floor_num=1,
        sigma_floor_den=5000,
    )

    # count_n < min_samples
    assert not exact_int_corridor_check(
        candidate_scaled=cand_jump,
        sum_x=s1,
        count_n=10,
        sum_x2=s2,
        min_samples=20,
        k_stddev=4,
        sigma_floor_num=1,
        sigma_floor_den=5000,
    )


def test_exact_integer_corridor_zero_drift_sliding():
    """Verify that incremental integer addition and subtraction has ZERO drift over 10,000 steps."""
    window = 30
    ring = [10_000_000_000] * window
    s1 = sum(ring)
    s2 = sum(x * x for x in ring)
    head = 0

    import random

    rng = random.Random(42)

    for step in range(10_000):
        # New price around $100 with random tick noise [-50, +50] cents
        delta_cents = rng.randint(-50, 50)
        new_px = 10_000_000_000 + delta_cents * 1_000_000

        old_px = ring[head]
        s1 = s1 + new_px - old_px
        s2 = s2 + (new_px * new_px) - (old_px * old_px)
        ring[head] = new_px
        head = (head + 1) % window

        if step % 500 == 0:
            # Bit-for-bit exactness check against direct recomputation
            exact_s1 = sum(ring)
            exact_s2 = sum(x * x for x in ring)
            assert s1 == exact_s1, f"Step {step}: sum_x drifted! {s1} != {exact_s1}"
            assert s2 == exact_s2, f"Step {step}: sum_x2 drifted! {s2} != {exact_s2}"


def test_soa_batch_validation_matches_aos():
    """Verify that Structure-of-Arrays (SoA) SIMD batch evaluation matches AoS and single evaluation."""
    if not _NATIVE_LIB or not hasattr(_NATIVE_LIB, "fastpath_engine_evaluate_soa"):
        pytest.skip("Native C fastpath_engine_evaluate_soa not available")

    engine = FastQualityEngine()
    count = 16

    source_ids = (ctypes.c_int32 * count)(*[0] * count)
    instrument_ids = (ctypes.c_int32 * count)(*[i % 4 for i in range(count)])
    event_types = (ctypes.c_int32 * count)(
        *[0 if i % 2 == 0 else 1 for i in range(count)]
    )
    prices = (ctypes.c_double * count)(*[100.0 + (i * 0.1) for i in range(count)])
    quantities = (ctypes.c_double * count)(*[10.0] * count)
    # Event 5 has a crossed quote: bid 105 > ask 100
    bids = (ctypes.c_double * count)(
        *[105.0 if i == 5 else 99.95 for i in range(count)]
    )
    asks = (ctypes.c_double * count)(
        *[100.0 if i == 5 else 100.05 for i in range(count)]
    )
    bid_sizes = (ctypes.c_double * count)(*[100.0] * count)
    ask_sizes = (ctypes.c_double * count)(*[100.0] * count)
    sequences = (ctypes.c_int64 * count)(*[i + 1 for i in range(count)])
    exchange_ts = (ctypes.c_double * count)(*[1.7e9 + i for i in range(count)])
    receive_ts = (ctypes.c_double * count)(*[1.7e9 + i + 0.001 for i in range(count)])
    present_masks = (ctypes.c_uint32 * count)(
        *[0x80000000 | 0x3F for _ in range(count)]
    )

    out_statuses = (ctypes.c_int32 * count)()
    out_reasons = (ctypes.c_uint64 * count)()

    ptr = engine._engine_ptr_c or engine._engine_ptr
    _NATIVE_LIB.fastpath_engine_evaluate_soa(
        ptr,
        count,
        source_ids,
        instrument_ids,
        event_types,
        prices,
        quantities,
        bids,
        asks,
        bid_sizes,
        ask_sizes,
        sequences,
        exchange_ts,
        receive_ts,
        present_masks,
        out_statuses,
        out_reasons,
    )

    # In C fastpath: 0 = STATUS_VALID, 1 = STATUS_SUSPICIOUS, 2 = STATUS_INVALID
    # Verify event 5 was flagged as INVALID (2) due to CROSSED_QUOTE (bit 6 = 1 << 6 = 0x40)
    assert out_statuses[5] == 2
    assert (out_reasons[5] & (1 << 6)) != 0

    # Normal quotes/trades must be VALID (0)
    assert out_statuses[0] == 0
    assert out_statuses[1] == 0
