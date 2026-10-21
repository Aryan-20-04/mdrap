"""Unit tests for MDRAP Clock, NanosecondClock, and PTPClock (IEEE 1588)."""

import time
import pytest
from mdrap.clock import (
    Clock,
    FixedClock,
    NanosecondClock,
    PrecisionClock,
    PTPClock,
    SystemClock,
    ns_to_seconds,
    seconds_to_ns,
)


def test_time_conversion_roundtrip():
    """Verify conversion between floating seconds and integer nanoseconds."""
    s = 1700000000.123456
    ns = seconds_to_ns(s)
    assert isinstance(ns, int)
    assert ns == 1700000000123456000

    s_back = ns_to_seconds(ns)
    assert abs(s - s_back) < 1e-9


def test_nanosecond_clock_protocols():
    """Verify NanosecondClock satisfies both Clock and PrecisionClock protocols."""
    clock = NanosecondClock()
    assert isinstance(clock, Clock)
    assert isinstance(clock, PrecisionClock)

    t_s = clock.now()
    t_ns = clock.now_ns()

    assert isinstance(t_s, float)
    assert isinstance(t_ns, int)
    assert t_ns > 0
    assert abs(t_s - (t_ns / 1e9)) < 0.1  # Within 100ms of each other


def test_fixed_clock_nanosecond_support():
    """Verify FixedClock supports nanosecond methods."""
    fc = FixedClock(100.0)
    assert fc.now() == 100.0
    assert fc.now_ns() == 100_000_000_000

    fc.advance_ns(500_000_000)  # +0.5s
    assert fc.now() == 100.5
    assert fc.now_ns() == 100_500_000_000

    fc.set_ns(200_000_000_000)
    assert fc.now() == 200.0


def test_ptp_clock_offset_and_drift_calibration():
    """Verify PTPClock accurately applies offset and frequency drift."""
    base = FixedClock(1_000.0)  # 1,000,000,000,000 ns
    # Offset of +250ns, drift of +1000 ppb (+1 ppm = +0.0001%)
    ptp = PTPClock(base_clock=base, offset_ns=250, drift_ppb=1000.0)

    # Expected: (1_000_000_000_000 + 250) * (1.0 + 1e-6)
    expected_ns = int((1_000_000_000_000 + 250) * 1.000001)
    assert ptp.now_ns() == expected_ns

    # Calibrate with new offset
    ptp.calibrate(measured_offset_ns=-500, measured_drift_ppb=0.0)
    assert ptp.offset_ns == -500
    assert ptp.drift_ppb == 0.0
    assert ptp.now_ns() == 1_000_000_000_000 - 500

    stats = ptp.get_stats()
    assert stats["offset_ns"] == -500
    assert stats["sync_count"] == 1
    assert stats["last_sync_ts"] > 0
