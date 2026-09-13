from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from vessel import (
    GLOBAL_CHOKEPOINTS,
    Vessel,
    VesselTracker,
    haversine_nm,
)


def test_haversine_nm():
    # London to Paris distance
    london_lat, london_lon = 51.5074, -0.1278
    paris_lat, paris_lon = 48.8566, 2.3522
    dist = haversine_nm(london_lat, london_lon, paris_lat, paris_lon)
    # Distance is roughly 185 nautical miles
    assert 180.0 <= dist <= 192.0

    # Same coordinate should be 0 distance
    assert haversine_nm(london_lat, london_lon, london_lat, london_lon) == 0.0


def test_chokepoints_catalog():
    assert len(GLOBAL_CHOKEPOINTS) >= 8
    expected_keys = {"hormuz", "malacca", "suez", "bab_el_mandeb", "panama", "bosphorus", "cape", "dover"}
    for k in expected_keys:
        assert k in GLOBAL_CHOKEPOINTS
        cp = GLOBAL_CHOKEPOINTS[k]
        assert cp.radius_nm > 0
        assert -90.0 <= cp.latitude <= 90.0
        assert -180.0 <= cp.longitude <= 180.0


def test_vessel_proximity_detection():
    # Vessel sitting right at Strait of Hormuz entrance (26.50 N, 56.30 E)
    v_hormuz = Vessel(
        imo="9990001",
        mmsi="123456789",
        name="TEST TANKER",
        vessel_type="Crude Oil Tanker (VLCC)",
        flag="Panama",
        dwt=300000,
        operator="Test Marine",
        charterer="Test Oil",
        commodity="Crude",
        cargo_volume="2M bbl",
        cargo_category="Crude Oil",
        laden_status="LADEN",
        origin_port="A",
        destination_port="B",
        eta="2026-09-20",
        latitude=26.50,
        longitude=56.30,
        speed_knots=12.0,
        heading=90.0,
        nav_status="Underway",
    )
    v_hormuz.update_chokepoint_proximity()
    assert v_hormuz.nearest_chokepoint == "Strait of Hormuz"
    assert v_hormuz.distance_to_chokepoint_nm < 20.0
    assert v_hormuz.in_chokepoint is True

    # Vessel in remote South Pacific
    v_remote = Vessel(
        imo="9990002",
        mmsi="123456780",
        name="PACIFIC ROAMER",
        vessel_type="Bulk Carrier",
        flag="Liberia",
        dwt=80000,
        operator="Test Marine",
        charterer="Test Cargo",
        commodity="Grain",
        cargo_volume="70k MT",
        cargo_category="Dry Bulk",
        laden_status="LADEN",
        origin_port="A",
        destination_port="B",
        eta="2026-09-20",
        latitude=-45.0,
        longitude=-140.0,
        speed_knots=10.0,
        heading=180.0,
        nav_status="Underway",
    )
    v_remote.update_chokepoint_proximity()
    assert v_remote.in_chokepoint is False
    assert v_remote.distance_to_chokepoint_nm > 1000.0


def test_vessel_filtering():
    tracker = VesselTracker()

    # Filter by operator
    fl_vessels = tracker.list_vessels(company="Frontline")
    assert len(fl_vessels) >= 3
    for v in fl_vessels:
        assert "frontline" in v.operator.lower() or "frontline" in v.charterer.lower()

    # Filter by charterer
    aramco_vessels = tracker.list_vessels(company="Aramco")
    assert len(aramco_vessels) >= 1
    assert any("Aramco" in v.charterer for v in aramco_vessels)

    # Filter by vessel_type
    lng_vessels = tracker.list_vessels(vessel_type="LNG")
    assert len(lng_vessels) >= 3
    for v in lng_vessels:
        assert "LNG" in v.vessel_type or "Natural Gas" in v.vessel_type

    # Filter by status
    ballast = tracker.list_vessels(laden_status="BALLAST")
    assert len(ballast) >= 1
    for v in ballast:
        assert v.laden_status == "BALLAST"

    # Filter by chokepoint
    suez_vessels = tracker.list_vessels(chokepoint="suez")
    assert len(suez_vessels) >= 1
    for v in suez_vessels:
        assert "suez" in v.nearest_chokepoint.lower()


def test_vessel_lookup():
    tracker = VesselTracker()

    # Look up by IMO
    v_imo = tracker.get_vessel("9745342")
    assert v_imo is not None
    assert v_imo.name == "FRONT ALTAIR"

    # Look up by MMSI
    v_mmsi = tracker.get_vessel("538006849")
    assert v_mmsi is not None
    assert v_mmsi.name == "FRONT ALTAIR"

    # Look up by Name case-insensitive
    v_name = tracker.get_vessel("front altair")
    assert v_name is not None
    assert v_name.imo == "9745342"

    # Non-existent
    assert tracker.get_vessel("NON_EXISTENT_SHIP_9999") is None


def test_commodity_breakdown():
    tracker = VesselTracker()
    breakdown = tracker.get_commodity_breakdown()

    assert breakdown["total_vessels_tracked"] >= 20
    assert breakdown["laden_vessels"] > 0
    assert breakdown["ballast_vessels"] >= 1
    assert breakdown["laden_vessels"] + breakdown["ballast_vessels"] == breakdown["total_vessels_tracked"]

    categories = breakdown["categories"]
    assert "Crude Oil" in categories
    assert "LNG" in categories
    assert "Dry Bulk" in categories
    assert "Container Cargo" in categories

    # Verify top charterers
    top_charterers = breakdown["top_charterers"]
    assert len(top_charterers) > 0
    assert any("Shell" in c[0] or "Aramco" in c[0] or "BP" in c[0] for c in top_charterers)


def test_update_position():
    tracker = VesselTracker()
    v = tracker.get_vessel("9745342")  # FRONT ALTAIR
    assert v is not None

    # Move vessel into Suez Canal coordinates
    tracker.update_position("9745342", lat=29.93, lon=32.55, speed=8.0, heading=350.0)
    assert v.latitude == 29.93
    assert v.longitude == 32.55
    assert v.speed_knots == 8.0
    assert v.heading == 350.0
    assert v.nearest_chokepoint == "Suez Canal"
    assert v.distance_to_chokepoint_nm == 0.0
    assert v.in_chokepoint is True
