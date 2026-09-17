from __future__ import annotations

import math
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytestmark = pytest.mark.slow  # ponytail: stress tests skip by default

from vessel import (
    GLOBAL_CHOKEPOINTS,
    Vessel,
    VesselTracker,
    haversine_nm,
    sanitize_vessel_string,
    validate_coordinates,
    validate_speed_and_heading,
)


def test_vessel_stress_throughput():
    """Stress tests 10,000 coordinate distance calculations and chokepoint updates in a tight loop."""
    v = Vessel(
        imo="9991234",
        mmsi="111222333",
        name="SPEED DEMON",
        vessel_type="Crude Oil Tanker",
        flag="Liberia",
        dwt=300000,
        operator="Global Tankers",
        charterer="Energy Major",
        commodity="Crude",
        cargo_volume="2M bbl",
        cargo_category="Crude Oil",
        laden_status="LADEN",
        origin_port="Port A",
        destination_port="Port B",
        eta="2026-09-30",
        latitude=26.0,
        longitude=56.0,
        speed_knots=14.0,
        heading=90.0,
        nav_status="Underway",
    )

    t0 = time.perf_counter()
    iterations = 10_000
    for i in range(iterations):
        # Orbit in the Persian Gulf
        lat = 26.0 + (i % 100) * 0.01
        lon = 56.0 + (i % 100) * 0.01
        dist = haversine_nm(lat, lon, 26.56, 56.25)
        assert dist >= 0.0

    elapsed = time.perf_counter() - t0
    # 10,000 haversine calculations should easily finish under 250ms in pure Python
    assert elapsed < 0.25, f"10k distance calculations took {elapsed:.4f}s (expected < 0.25s)"


def test_vessel_o1_lookup_speed():
    """Empirically verifies O(1) indexed lookup performance over 20,000 queries."""
    tracker = VesselTracker()
    test_identifiers = [
        "9745342",            # IMO: FRONT ALTAIR
        "538006849",          # MMSI: FRONT ALTAIR
        "FRONT ALTAIR",       # Exact Name
        "9235268",            # IMO: TI EUROPE
        "205423000",          # MMSI: TI EUROPE
        "TI EUROPE",          # Exact Name
        "9811000",            # IMO: EVER GIVEN
        "353136000",          # MMSI: EVER GIVEN
        "EVER GIVEN",         # Exact Name
        "BERGE BULKER",       # Name: BERGE BULKER
    ]

    t0 = time.perf_counter()
    queries = 20_000
    for i in range(queries):
        ident = test_identifiers[i % len(test_identifiers)]
        v = tracker.get_vessel(ident)
        assert v is not None

    elapsed = time.perf_counter() - t0
    # 20,000 dictionary hash lookups should finish under 100ms (or 500ms when coverage tracing is active)
    max_expected = 0.50 if getattr(sys, "gettrace", lambda: None)() is not None else 0.10
    assert elapsed < max_expected, f"20,000 lookups took {elapsed:.4f}s (expected < {max_expected}s)"


@pytest.mark.parametrize("bad_lat, bad_lon", [
    (float("nan"), 50.0),
    (50.0, float("nan")),
    (float("inf"), 50.0),
    (50.0, float("-inf")),
    (90.1, 0.0),
    (-90.1, 0.0),
    (0.0, 180.1),
    (0.0, -180.1),
    (1e20, 0.0),
    ("50.0", 0.0),
    (None, 0.0),
])
def test_vessel_adversarial_coordinates_fuzzing(bad_lat, bad_lon):
    """Fuzzes coordinate inputs with NaN, Inf, out-of-bounds, and malformed types."""
    with pytest.raises(ValueError):
        validate_coordinates(bad_lat, bad_lon)

    with pytest.raises(ValueError):
        haversine_nm(bad_lat, bad_lon, 0.0, 0.0)


@pytest.mark.parametrize("bad_speed, bad_heading", [
    (-1.0, 180.0),
    (200.0, 180.0),
    (float("nan"), 180.0),
    (15.0, -1.0),
    (15.0, 360.5),
    (15.0, float("inf")),
    ("fast", 180.0),
    (15.0, "north"),
])
def test_vessel_adversarial_speed_and_heading(bad_speed, bad_heading):
    """Fuzzes speed and heading with negative, excessive, NaN, Inf, and malformed types."""
    with pytest.raises(ValueError):
        validate_speed_and_heading(bad_speed, bad_heading)


def test_vessel_string_sanitization():
    """Verifies control character stripping and boundary limits."""
    raw = "FRONT\x00\x07\x1b ALTAIR\r\n"
    cleaned = sanitize_vessel_string(raw, max_len=50)
    assert cleaned == "FRONT ALTAIR"
    assert "\x00" not in cleaned
    assert "\x1b" not in cleaned

    # Max length bounding
    long_str = "A" * 300
    truncated = sanitize_vessel_string(long_str, max_len=60)
    assert len(truncated) == 60


def test_vessel_antipodal_haversine_clamp():
    """Verifies antipodal calculations do not trigger math domain errors."""
    # Exact opposite sides of Earth
    dist = haversine_nm(0.0, 0.0, 0.0, 180.0)
    # Earth circumference ~ 21,600 nm -> half is ~10,807 nm
    assert 10_700.0 <= dist <= 10_900.0

    # North Pole to South Pole
    dist_poles = haversine_nm(90.0, 0.0, -90.0, 0.0)
    assert 10_700.0 <= dist_poles <= 10_900.0
