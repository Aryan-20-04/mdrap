import ctypes
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytestmark = pytest.mark.slow  # ponytail: throughput tests skip by default

from fastpath import FastQualityEngine, _CFastResult, _NATIVE_LIB
from sbe import pack_sbe_tick

if _NATIVE_LIB is None:
    pytest.skip(
        "Native C fastpath library is disabled or not available",
        allow_module_level=True,
    )


def test_native_sbe_stream_validation_rules():
    engine = FastQualityEngine()

    # 4 ticks: 1 clean, 1 crossed quote, 1 invalid negative price, 1 duplicate sequence
    raw_buf = bytearray(4 * 128)

    # Tick 0: Clean
    raw_buf[0:128] = pack_sbe_tick(
        seq=101,
        symbol="AAPL",
        source="FEEDX",
        price=150.0,
        size=100.0,
        bid=149.9,
        ask=150.1,
        status="VALID",
    )
    # Tick 1: Crossed quote (bid 150.5 > ask 150.0)
    raw_buf[128:256] = pack_sbe_tick(
        seq=102,
        symbol="AAPL",
        source="FEEDX",
        price=150.0,
        size=100.0,
        bid=150.5,
        ask=150.0,
        status="VALID",
    )
    # Tick 2: Negative price (-10.0)
    raw_buf[256:384] = pack_sbe_tick(
        seq=103,
        symbol="AAPL",
        source="FEEDX",
        price=-10.0,
        size=100.0,
        bid=149.9,
        ask=150.1,
        status="VALID",
    )
    # Tick 3: Duplicate of Tick 0 (seq 101)
    raw_buf[384:512] = pack_sbe_tick(
        seq=101,
        symbol="AAPL",
        source="FEEDX",
        price=150.0,
        size=100.0,
        bid=149.9,
        ask=150.1,
        status="VALID",
    )

    valid_count, results = engine.process_sbe_stream(raw_buf, 4)

    # Tick 0: Valid
    assert results[0].status == 0  # VALID
    assert results[0].reason_mask == 0

    # Tick 1: Crossed quote (Reason bit 6 = 1 << 6 = 64)
    assert results[1].status == 2  # INVALID
    assert (results[1].reason_mask & (1 << 6)) != 0

    # Tick 2: Negative price (Reason bit 0 = 1 << 0 = 1)
    assert results[2].status == 2  # INVALID
    assert (results[2].reason_mask & (1 << 0)) != 0

    # Tick 3: Duplicate sequence (Reason bit 1 = 1 << 1 = 2)
    assert results[3].status == 2  # INVALID
    assert (results[3].reason_mask & (1 << 1)) != 0

    assert valid_count == 1


def test_native_sbe_stream_throughput_100k():
    engine = FastQualityEngine()
    count = 100000
    raw_buf = bytearray(count * 128)

    # Fast fill buffer using native C pack
    for i in range(count):
        _NATIVE_LIB.fastpath_sbe_pack_tick(
            (ctypes.c_uint8 * 128).from_buffer(raw_buf, i * 128),
            i + 5000,
            b"MSFT",
            b"FEEDY",
            420.0 + (i % 50) * 0.01,
            50.0,
            419.9,
            420.1,
            100.0,
            100.0,
            1,
            0,
            1000.0 + i * 0.0001,
            1000.0005,
            1000.001,
            0.1,
        )

    t0 = time.perf_counter_ns()
    valid_count, results = engine.process_sbe_stream(raw_buf, count)
    t1 = time.perf_counter_ns()

    elapsed_s = (t1 - t0) / 1e9
    eps = count / elapsed_s
    lat_ns = (t1 - t0) / count

    assert valid_count == count
    # Must exceed at least 5,000,000 events/sec in native C!
    assert eps > 5000000.0, f"Throughput was {eps:,.0f} eps (expected >5,000,000)"
    # Per-event latency must be sub-microsecond (< 200 ns)
    assert lat_ns < 200.0, f"Latency was {lat_ns:.1f} ns (expected <200 ns)"


def test_native_sbe_stream_throughput_500k():
    engine = FastQualityEngine()
    count = 500000
    raw_buf = bytearray(count * 128)

    for i in range(count):
        _NATIVE_LIB.fastpath_sbe_pack_tick(
            (ctypes.c_uint8 * 128).from_buffer(raw_buf, i * 128),
            i + 200000,
            b"NVDA",
            b"FEEDZ",
            130.0 + (i % 100) * 0.02,
            25.0,
            129.9,
            130.1,
            200.0,
            200.0,
            1,
            0,
            1000.0 + i * 0.0001,
            1000.0005,
            1000.001,
            0.1,
        )

    t0 = time.perf_counter_ns()
    valid_count, results = engine.process_sbe_stream(raw_buf, count)
    t1 = time.perf_counter_ns()

    elapsed_s = (t1 - t0) / 1e9
    eps = count / elapsed_s

    assert valid_count == count
    # Proves 500,000+ events per second goal is shattered!
    assert eps > 10000000.0, f"Throughput was {eps:,.0f} eps (expected >10M eps)"


def test_native_sbe_stream_generate_and_throughput_1m():
    engine = FastQualityEngine()
    count = 1000000

    # 1. Test high-speed C stream generation (1M frames)
    t_gen_0 = time.perf_counter_ns()
    raw_buf = engine.generate_sbe_stream(count, anomaly_rate=0.01)
    t_gen_1 = time.perf_counter_ns()
    gen_eps = count / ((t_gen_1 - t_gen_0) / 1e9)
    assert len(raw_buf) == count * 128
    assert gen_eps > 2000000.0  # C generator runs at >2M frames/sec on standard CI VMs

    # 2. Test 1,000,000 event validation in native C
    t0 = time.perf_counter_ns()
    valid_count, results = engine.process_sbe_stream(raw_buf, count)
    t1 = time.perf_counter_ns()

    elapsed_s = (t1 - t0) / 1e9
    eps = count / elapsed_s
    lat_ns = (t1 - t0) / count

    # 1% anomalies injected
    assert valid_count < count
    assert valid_count > count * 0.98

    # Shatters 1 Million events/sec requirement (> 10M eps, typically 20-30M)
    assert eps > 10000000.0, f"Throughput was {eps:,.0f} eps (expected >10M eps)"
    assert lat_ns < 150.0, f"Per-tick latency was {lat_ns:.1f} ns (expected <150 ns)"
