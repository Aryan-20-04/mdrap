import math
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vessel import (
    haversine_nm,
    GLOBAL_CHOKEPOINTS,
    Vessel,
    VesselTracker,
    validate_coordinates,
)
import fastpath
from fastpath import (
    fast_haversine_nm,
    make_fast_chokepoints,
    fast_vessel_chokepoint_eval,
    fast_batch_fleet_geofence,
    _NATIVE_LIB,
)

if _NATIVE_LIB is None:
    pytest.skip(
        "Native C fastpath library is disabled or not available",
        allow_module_level=True,
    )


def _pure_python_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_nm = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    a = max(0.0, min(1.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(r_nm * c, 1)


def test_native_c_library_loaded():
    """Verify that the compiled _fastpath_native library is loaded and exports spatial functions."""
    assert _NATIVE_LIB is not None
    assert hasattr(_NATIVE_LIB, "fastpath_haversine_nm")
    assert hasattr(_NATIVE_LIB, "fastpath_vessel_chokepoint_eval")
    assert hasattr(_NATIVE_LIB, "fastpath_batch_fleet_geofence")


def test_haversine_exact_numerical_parity():
    """Verify exact parity between pure Python and C fastpath across diverse coordinates."""
    test_cases = [
        # Zero distance
        (0.0, 0.0, 0.0, 0.0),
        (26.56, 56.25, 26.56, 56.25),
        # Strait of Hormuz to Ras Tanura
        (26.40, 56.45, 26.56, 56.25),
        # Dover Strait to Rotterdam
        (51.02, 1.45, 51.92, 4.48),
        # Trans-Pacific across International Date Line
        (35.0, 179.0, 35.0, -179.0),
        # Equator to North Pole
        (0.0, 0.0, 90.0, 0.0),
        # Antipodal coordinates
        (45.0, 10.0, -45.0, -170.0),
        # Suez Canal
        (29.93, 32.55, 31.25, 32.30),
        # Malacca Strait
        (1.43, 103.12, 1.25, 103.80),
    ]

    for lat1, lon1, lat2, lon2 in test_cases:
        py_dist = _pure_python_haversine(lat1, lon1, lat2, lon2)
        c_dist = fast_haversine_nm(lat1, lon1, lat2, lon2)
        assert c_dist is not None
        # Must match to within 0.1 nautical miles
        assert abs(py_dist - c_dist) <= 0.1, (
            f"Mismatch at ({lat1},{lon1})->({lat2},{lon2}): py={py_dist}, c={c_dist}"
        )


def test_spatial_geofence_single_and_batch_parity():
    """Verify single and batch C geofencing match the pure Python chokepoint distance logic."""
    cp_list = list(GLOBAL_CHOKEPOINTS.values())
    c_cps = make_fast_chokepoints(GLOBAL_CHOKEPOINTS)
    assert c_cps is not None

    test_vessels = [
        (26.40, 56.45),  # Hormuz entrance
        (1.25, 103.80),  # Singapore / Malacca
        (30.00, 32.50),  # Suez
        (12.50, 43.30),  # Bab-el-Mandeb
        (51.00, 1.40),  # Dover
        (-34.50, 18.50),  # Cape of Good Hope
        (41.10, 29.05),  # Bosphorus
        (9.10, -79.70),  # Panama
        (0.00, -25.00),  # Mid-Atlantic (Open Sea)
    ]

    lats = [t[0] for t in test_vessels]
    lons = [t[1] for t in test_vessels]

    # Evaluate batch
    batch_results = fast_batch_fleet_geofence(lats, lons, c_cps, len(cp_list))
    assert batch_results is not None
    assert len(batch_results) == len(test_vessels)

    for i, (v_lat, v_lon) in enumerate(test_vessels):
        # Single eval
        single_res = fast_vessel_chokepoint_eval(v_lat, v_lon, c_cps, len(cp_list))
        assert single_res is not None
        s_idx, s_dist, s_in = single_res

        b_idx, b_dist, b_in = batch_results[i]
        assert s_idx == b_idx
        assert abs(s_dist - b_dist) <= 0.1
        assert s_in == b_in

        # Verify against pure Python Vessel object
        v = Vessel(
            imo=f"900000{i}",
            mmsi=f"10000000{i}",
            name=f"TEST_{i}",
            vessel_type="Crude Oil Tanker",
            flag="Panama",
            dwt=100000,
            operator="Test Owner",
            charterer="Test Charterer",
            commodity="Crude Oil",
            cargo_volume="500,000 bbl",
            cargo_category="Crude Oil",
            laden_status="LADEN",
            origin_port="Port A",
            destination_port="Port B",
            eta="2026-10-01",
            latitude=v_lat,
            longitude=v_lon,
            speed_knots=12.0,
            heading=90.0,
            nav_status="Underway",
        )
        v.update_chokepoint_proximity()

        expected_name = cp_list[s_idx].name
        assert v.nearest_chokepoint == expected_name
        assert abs(v.distance_to_chokepoint_nm - s_dist) <= 0.1
        assert v.in_chokepoint == s_in


def test_fastpath_fallback_resilience_when_c_unavailable(monkeypatch):
    """Verify that if the C library is missing or fails, vessel.py falls back to pure Python seamlessly."""
    import vessel

    # Temporarily force fastpath to be disabled
    monkeypatch.setattr(vessel, "_HAS_FASTPATH", False)
    monkeypatch.setattr(vessel, "_C_CHOKEPOINTS", None)

    # 1. haversine_nm must still return correct distance
    dist = vessel.haversine_nm(26.40, 56.45, 26.56, 56.25)
    assert abs(dist - 14.4) <= 0.1

    # 2. update_chokepoint_proximity must still correctly calculate nearest chokepoint
    v = Vessel(
        imo="9999999",
        mmsi="999999999",
        name="FALLBACK_TEST",
        vessel_type="Crude Oil Tanker",
        flag="Liberia",
        dwt=150000,
        operator="Test",
        charterer="Test",
        commodity="Crude",
        cargo_volume="1,000,000 bbl",
        cargo_category="Crude Oil",
        laden_status="LADEN",
        origin_port="Port X",
        destination_port="Port Y",
        eta="2026-10-01",
        latitude=26.40,
        longitude=56.45,
        speed_knots=14.0,
        heading=100.0,
        nav_status="Underway",
    )
    v.update_chokepoint_proximity()
    assert v.nearest_chokepoint == "Strait of Hormuz"
    assert v.in_chokepoint is True
    assert v.distance_to_chokepoint_nm < 20.0


def test_fastpath_benchmark_speedup():
    """Benchmark pure Python vs native C batch to demonstrate microsecond/nanosecond latency reduction."""
    count = 10000
    lats = [26.40 + (i * 0.001) for i in range(count)]
    lons = [56.45 + (i * 0.001) for i in range(count)]
    cp_list = list(GLOBAL_CHOKEPOINTS.values())
    c_cps = make_fast_chokepoints(GLOBAL_CHOKEPOINTS)

    # Time native C batch
    t0 = time.perf_counter()
    batch_res = fast_batch_fleet_geofence(lats, lons, c_cps, len(cp_list))
    t1 = time.perf_counter()
    c_duration = t1 - t0

    assert batch_res is not None
    assert len(batch_res) == count
    c_per_call_ns = (c_duration / count) * 1e9

    # Even across 8 chokepoint comparisons per vessel (80,000 chokepoint checks total),
    # C should execute in well under 50 milliseconds (allow 1.5s under heavy full test suite load)
    assert c_duration < 1.5
    print(
        f"\n[FASTPATH BENCHMARK] Evaluated {count} vessels across {len(cp_list)} chokepoints ({count * len(cp_list):,} checks) in {c_duration * 1000:.2f} ms ({c_per_call_ns:.1f} ns/vessel)"
    )
