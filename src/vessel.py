from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastpath import (
        fast_haversine_nm,
        make_fast_chokepoints,
        fast_vessel_chokepoint_eval,
        fast_batch_fleet_geofence,
    )
    _HAS_FASTPATH = True
except (ImportError, Exception):
    _HAS_FASTPATH = False

CONTROL_CHAR_REGEX = re.compile(r'[\x00-\x1f\x7f]')


def validate_coordinates(lat: float, lon: float) -> Tuple[float, float]:
    """Validates GPS coordinates ensuring finite numeric values within realistic geo boundaries."""
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        raise ValueError(f"Latitude and longitude must be numbers (got {type(lat).__name__}, {type(lon).__name__})")
    if math.isnan(lat) or math.isinf(lat):
        raise ValueError(f"Invalid latitude value: {lat}")
    if math.isnan(lon) or math.isinf(lon):
        raise ValueError(f"Invalid longitude value: {lon}")
    if not (-90.0 <= float(lat) <= 90.0):
        raise ValueError(f"Latitude {lat} out of valid range [-90.0, 90.0]")
    if not (-180.0 <= float(lon) <= 180.0):
        raise ValueError(f"Longitude {lon} out of valid range [-180.0, 180.0]")
    return float(lat), float(lon)


def validate_speed_and_heading(
    speed: Optional[float], heading: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """Validates nautical vessel speed (knots) and navigational heading (degrees)."""
    valid_speed = None
    if speed is not None:
        if not isinstance(speed, (int, float)):
            raise ValueError(f"Speed must be a number (got {type(speed).__name__})")
        if math.isnan(speed) or math.isinf(speed):
            raise ValueError(f"Invalid speed value: {speed}")
        if speed < 0.0 or speed > 150.0:
            raise ValueError(f"Speed {speed} knots out of realistic maritime bounds [0, 150]")
        valid_speed = float(speed)

    valid_heading = None
    if heading is not None:
        if not isinstance(heading, (int, float)):
            raise ValueError(f"Heading must be a number (got {type(heading).__name__})")
        if math.isnan(heading) or math.isinf(heading):
            raise ValueError(f"Invalid heading value: {heading}")
        if heading < 0.0 or heading > 360.0:
            raise ValueError(f"Heading {heading} degrees out of circular range [0, 360]")
        valid_heading = float(heading)

    return valid_speed, valid_heading


def sanitize_vessel_string(val: str, max_len: int = 120) -> str:
    """Strips control characters, bounds length, and sanitizes input strings to protect non-public data."""
    if not isinstance(val, str):
        val = str(val)
    return CONTROL_CHAR_REGEX.sub('', val).strip()[:max_len]


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates Great-Circle distance between two GPS coordinates in nautical miles."""
    lat1, lon1 = validate_coordinates(lat1, lon1)
    lat2, lon2 = validate_coordinates(lat2, lon2)
    if _HAS_FASTPATH:
        res = fast_haversine_nm(lat1, lon1, lat2, lon2)
        if res is not None:
            return res

    r_nm = 3440.065  # Earth radius in nautical miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    # Clamp 'a' to [0.0, 1.0] to prevent math domain errors from floating-point inaccuracies
    a = max(0.0, min(1.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(r_nm * c, 1)


@dataclass
class Chokepoint:
    name: str
    code: str
    latitude: float
    longitude: float
    radius_nm: float
    significance: str
    daily_flow: str
    lat_rad: float = field(init=False, default=0.0)
    lon_rad: float = field(init=False, default=0.0)
    cos_lat: float = field(init=False, default=0.0)
    sin_lat: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.lat_rad = math.radians(self.latitude)
        self.lon_rad = math.radians(self.longitude)
        self.cos_lat = math.cos(self.lat_rad)
        self.sin_lat = math.sin(self.lat_rad)


GLOBAL_CHOKEPOINTS: Dict[str, Chokepoint] = {
    "hormuz": Chokepoint(
        name="Strait of Hormuz",
        code="HORMUZ",
        latitude=26.56,
        longitude=56.25,
        radius_nm=75.0,
        significance="21% of global petroleum consumption, Persian Gulf outlet",
        daily_flow="~20.5M bbl/day crude + 20% global LNG",
    ),
    "malacca": Chokepoint(
        name="Strait of Malacca",
        code="MALACCA",
        latitude=1.43,
        longitude=103.12,
        radius_nm=85.0,
        significance="Primary Asian energy and manufactured goods conduit",
        daily_flow="~16.0M bbl/day crude + major container route",
    ),
    "suez": Chokepoint(
        name="Suez Canal",
        code="SUEZ",
        latitude=29.93,
        longitude=32.55,
        radius_nm=60.0,
        significance="Europe-Asia maritime shortcut, eliminating Africa circumnavigation",
        daily_flow="~12% of global seaborne trade volume",
    ),
    "bab_el_mandeb": Chokepoint(
        name="Bab-el-Mandeb Strait",
        code="BAB_EL_MANDEB",
        latitude=12.58,
        longitude=43.33,
        radius_nm=65.0,
        significance="Red Sea & Gulf of Aden gateway connecting to Suez",
        daily_flow="~8.8M bbl/day crude and refined petroleum products",
    ),
    "panama": Chokepoint(
        name="Panama Canal",
        code="PANAMA",
        latitude=9.08,
        longitude=-79.68,
        radius_nm=45.0,
        significance="Atlantic-Pacific link for Americas LNG, grains, and containers",
        daily_flow="~5% of world maritime trade, key US Gulf to Asia route",
    ),
    "bosphorus": Chokepoint(
        name="Bosphorus Strait",
        code="BOSPHORUS",
        latitude=41.12,
        longitude=29.07,
        radius_nm=35.0,
        significance="Black Sea oil export and Eurasian grain trade bottleneck",
        daily_flow="~3.0M bbl/day crude + Black Sea agriculture",
    ),
    "cape": Chokepoint(
        name="Cape of Good Hope",
        code="CAPE",
        latitude=-34.35,
        longitude=18.47,
        radius_nm=100.0,
        significance="Southern Africa detour during Red Sea / Suez disruptions",
        daily_flow="Major transit for mega-bulk (Valemax) and diverted tankers",
    ),
    "dover": Chokepoint(
        name="Dover Strait",
        code="DOVER",
        latitude=51.02,
        longitude=1.45,
        radius_nm=50.0,
        significance="European industrial artery, busiest shipping channel globally",
        daily_flow="500+ commercial vessels transiting daily",
    ),
}

_CHOKEPOINTS_LIST = list(GLOBAL_CHOKEPOINTS.values())
_C_CHOKEPOINTS = make_fast_chokepoints(GLOBAL_CHOKEPOINTS) if _HAS_FASTPATH else None


@dataclass
class Vessel:
    imo: str
    mmsi: str
    name: str
    vessel_type: str
    flag: str
    dwt: int
    operator: str
    charterer: str
    commodity: str
    cargo_volume: str
    cargo_category: str
    laden_status: str
    origin_port: str
    destination_port: str
    eta: str
    latitude: float
    longitude: float
    speed_knots: float
    heading: float
    nav_status: str
    nearest_chokepoint: str = "Open Sea"
    distance_to_chokepoint_nm: float = 9999.0
    in_chokepoint: bool = False
    last_update: str = ""

    def __post_init__(self) -> None:
        self.imo = sanitize_vessel_string(str(self.imo), 20)
        self.mmsi = sanitize_vessel_string(str(self.mmsi), 20)
        self.name = sanitize_vessel_string(self.name, 80)
        self.operator = sanitize_vessel_string(self.operator, 80)
        self.charterer = sanitize_vessel_string(self.charterer, 80)
        self.commodity = sanitize_vessel_string(self.commodity, 80)
        self.origin_port = sanitize_vessel_string(self.origin_port, 80)
        self.destination_port = sanitize_vessel_string(self.destination_port, 80)
        self.latitude, self.longitude = validate_coordinates(self.latitude, self.longitude)
        sp, hd = validate_speed_and_heading(self.speed_knots, self.heading)
        if sp is not None:
            self.speed_knots = sp
        if hd is not None:
            self.heading = hd

    def update_chokepoint_proximity(self) -> None:
        """Evaluates distances to all global maritime chokepoints and sets closest."""
        if _HAS_FASTPATH and _C_CHOKEPOINTS is not None:
            res = fast_vessel_chokepoint_eval(
                self.latitude, self.longitude, _C_CHOKEPOINTS, len(_CHOKEPOINTS_LIST)
            )
            if res is not None:
                idx, dist, inside = res
                self.nearest_chokepoint = _CHOKEPOINTS_LIST[idx].name
                self.distance_to_chokepoint_nm = dist
                self.in_chokepoint = inside
                return

        closest_cp = "Open Sea"
        min_dist = float("inf")
        inside = False

        r_nm = 3440.065
        phi1 = math.radians(self.latitude)
        cos_phi1 = math.cos(phi1)
        lon1_rad = math.radians(self.longitude)

        for cp in GLOBAL_CHOKEPOINTS.values():
            delta_phi = cp.lat_rad - phi1
            delta_lambda = cp.lon_rad - lon1_rad
            a = (
                math.sin(delta_phi * 0.5) ** 2
                + cos_phi1 * cp.cos_lat * math.sin(delta_lambda * 0.5) ** 2
            )
            a = max(0.0, min(1.0, a))
            dist = round(r_nm * (2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))), 1)

            if dist < min_dist:
                min_dist = dist
                closest_cp = cp.name
                if dist <= cp.radius_nm:
                    inside = True

        self.nearest_chokepoint = closest_cp
        self.distance_to_chokepoint_nm = min_dist
        self.in_chokepoint = inside


_FLEET_FIELDS = (
    "imo", "mmsi", "name", "vessel_type", "flag", "dwt", "operator", "charterer",
    "commodity", "cargo_volume", "cargo_category", "laden_status", "origin_port",
    "destination_port", "eta", "latitude", "longitude", "speed_knots", "heading",
    "nav_status", "last_update",
)

_FLEET_RECORDS = [
    ("9745342", "538006849", "FRONT ALTAIR", "Crude Oil Tanker (VLCC)", "Marshall Islands", 299999, "Frontline Ltd", "Saudi Aramco", "Arab Light Crude", "2,050,000 bbl", "Crude Oil", "LADEN", "Ras Tanura, Saudi Arabia", "Chiba, Japan", "2026-09-24 14:00 UTC", 26.40, 56.45, 13.8, 112.0, "Underway using Engine", "2026-09-12 16:30 UTC"),
    ("9235268", "205423000", "TI EUROPE", "Ultra Large Crude Carrier (ULCC)", "Belgium", 441585, "Euronav NV", "Vitol Group", "Basrah Heavy Crude", "3,100,000 bbl", "Crude Oil", "LADEN", "Basra Oil Terminal, Iraq", "Ningbo-Zhoushan, China", "2026-09-28 08:00 UTC", 1.35, 103.30, 12.5, 98.0, "Underway using Engine", "2026-09-12 17:15 UTC"),
    ("9722792", "477169400", "DHT JAGUAR", "Crude Oil Tanker (VLCC)", "Hong Kong", 319999, "DHT Holdings", "BP Shipping", "Brent Blend Crude", "2,000,000 bbl", "Crude Oil", "LADEN", "Hound Point, United Kingdom", "Rotterdam, Netherlands", "2026-09-13 18:00 UTC", 51.15, 1.55, 11.2, 55.0, "Underway using Engine", "2026-09-12 17:40 UTC"),
    ("9246633", "205424000", "OCEANIA", "Ultra Large Crude Carrier (ULCC)", "Belgium", 441561, "Euronav NV", "Shell Trading", "Murban Crude", "3,000,000 bbl", "Crude Oil", "LADEN", "Fujairah, UAE", "Singapore Anchorage", "2026-09-14 06:00 UTC", 1.22, 103.78, 0.5, 210.0, "At Anchor", "2026-09-12 16:55 UTC"),
    ("9595151", "538004501", "SEAWAYS KILIMANJARO", "Crude Oil Tanker (Suezmax)", "Marshall Islands", 158574, "International Seaways", "ExxonMobil", "Maya Heavy Crude", "1,000,000 bbl", "Crude Oil", "LADEN", "Coatzacoalcos, Mexico", "Texas City, USA", "2026-09-15 12:00 UTC", 26.85, -94.20, 13.0, 340.0, "Underway using Engine", "2026-09-12 15:20 UTC"),
    ("9411654", "311026600", "TEEKAY SPIRIT", "Crude Oil Tanker (Aframax)", "Bahamas", 115000, "Teekay Tankers", "Chevron", "CPC Blend Crude", "850,000 bbl", "Crude Oil", "LADEN", "Novorossiysk, Russia", "Trieste, Italy", "2026-09-17 09:00 UTC", 41.18, 29.05, 8.4, 195.0, "Underway using Engine", "2026-09-12 17:05 UTC"),
    ("9766217", "563032800", "HAFNIA SHANGHAI", "Product Tanker (LR1)", "Singapore", 74999, "Hafnia Ltd", "Trafigura", "Ultra Low Sulfur Diesel (ULSD)", "320,000 bbl", "Refined Products", "LADEN", "Sikka / Jamnagar, India", "Rotterdam, Netherlands", "2026-09-22 20:00 UTC", 29.85, 32.58, 10.1, 335.0, "Underway using Engine", "2026-09-12 17:30 UTC"),
    ("9797204", "538008129", "FRONT HERCULES", "Crude Oil Tanker (VLCC)", "Marshall Islands", 300000, "Frontline Ltd", "TotalEnergies", "Forties Crude", "2,000,000 bbl", "Crude Oil", "LADEN", "Hound Point, UK", "Qingdao, China", "2026-10-04 10:00 UTC", -34.40, 18.55, 14.1, 105.0, "Underway using Engine", "2026-09-12 14:10 UTC"),
    ("9732553", "311000523", "NORDIC AMERICAN STAR", "Crude Oil Tanker (Suezmax)", "Bahamas", 158000, "Nordic American Tankers", "Glencore", "Urals Crude", "1,000,000 bbl", "Crude Oil", "LADEN", "Primorsk, Russia", "Vadinar, India", "2026-09-20 16:00 UTC", 12.50, 43.40, 13.5, 130.0, "Underway using Engine", "2026-09-12 16:45 UTC"),
    ("9683051", "538005391", "SCORPIO POLARIS", "Product Tanker (LR2)", "Marshall Islands", 109999, "Scorpio Tankers", "Phillips 66", "Aviation Jet Fuel (Jet A-1)", "350,000 bbl", "Refined Products", "LADEN", "Ulsan, South Korea", "Los Angeles, USA", "2026-09-23 04:00 UTC", 33.20, -135.50, 13.9, 88.0, "Underway using Engine", "2026-09-12 13:50 UTC"),
    ("9886495", "538009214", "FRONT DUCHESS", "Crude Oil Tanker (VLCC)", "Marshall Islands", 299888, "Frontline Ltd", "Sinopec", "Unladen Ballast", "0 bbl", "Crude Oil", "BALLAST", "Ningbo, China", "Mina Al Ahmadi, Kuwait", "2026-09-21 11:00 UTC", 10.10, 68.50, 14.8, 295.0, "Underway using Engine", "2026-09-12 15:10 UTC"),
    ("9443683", "538003417", "AL DAFNA", "Liquefied Natural Gas Carrier (Q-Max)", "Marshall Islands", 161900, "QatarEnergy LNG", "JERA Japan", "Liquefied Natural Gas (LNG)", "266,000 m³ LNG", "LNG", "LADEN", "Ras Laffan, Qatar", "Futtsu, Japan", "2026-09-22 08:00 UTC", 1.40, 103.15, 17.2, 110.0, "Underway using Engine", "2026-09-12 17:20 UTC"),
    ("9687019", "310738000", "GASLOG GLASGOW", "Liquefied Natural Gas Carrier", "Bermuda", 89500, "GasLog Ltd", "Cheniere Energy", "Sabine Pass Liquefied Natural Gas", "174,000 m³ LNG", "LNG", "LADEN", "Sabine Pass, USA", "Tokyo Bay, Japan", "2026-09-29 18:00 UTC", 9.12, -79.72, 6.0, 180.0, "Underway using Engine", "2026-09-12 17:10 UTC"),
    ("7361934", "538002685", "GOLAR FREEZE", "Liquefied Natural Gas Carrier", "Marshall Islands", 72000, "Golar LNG", "Eni", "Coral South Liquefied Natural Gas", "125,000 m³ LNG", "LNG", "LADEN", "Pemba, Mozambique", "Rovigo LNG Terminal, Italy", "2026-09-24 14:00 UTC", 12.60, 43.35, 15.5, 325.0, "Underway using Engine", "2026-09-12 17:00 UTC"),
    ("9851608", "563102400", "BW MAGNOLIA", "Liquefied Natural Gas Carrier", "Singapore", 92000, "BW LNG", "Shell Trading", "Prelude FLNG", "174,000 m³ LNG", "LNG", "LADEN", "Darwin, Australia", "Incheon, South Korea", "2026-09-18 22:00 UTC", 21.50, 123.40, 16.8, 10.0, "Underway using Engine", "2026-09-12 16:15 UTC"),
    ("9817743", "232014450", "BERGE BULKER", "Very Large Ore Carrier (VLOC)", "United Kingdom", 261000, "Berge Bulk", "Rio Tinto", "Pilbara Iron Ore Fines", "260,000 MT Iron Ore", "Dry Bulk", "LADEN", "Dampier, Australia", "Qingdao, China", "2026-09-20 12:00 UTC", -4.20, 105.80, 12.8, 20.0, "Underway using Engine", "2026-09-12 15:45 UTC"),
    ("9488918", "538004128", "VALE BRASIL", "Valemax Very Large Ore Carrier", "Marshall Islands", 402347, "Vale Shipping", "Baosteel", "Carajás Iron Ore Pellets", "400,000 MT Iron Ore", "Dry Bulk", "LADEN", "Ponta da Madeira, Brazil", "Sohar, Oman", "2026-09-30 08:00 UTC", -34.45, 18.40, 13.2, 95.0, "Underway using Engine", "2026-09-12 14:30 UTC"),
    ("9522142", "538004219", "STAR BULK POLARIS", "Kamsarmax Dry Bulk Carrier", "Marshall Islands", 82000, "Star Bulk Carriers", "Cargill", "US Gulf Export Soybeans", "80,000 MT Grain", "Dry Bulk", "LADEN", "New Orleans, USA", "Alexandria, Egypt", "2026-09-25 15:00 UTC", 34.10, -40.50, 11.9, 85.0, "Underway using Engine", "2026-09-12 13:15 UTC"),
    ("9811000", "353136000", "EVER GIVEN", "Ultra Large Container Vessel (ULCV)", "Panama", 199629, "Evergreen Marine", "Walmart / Retail Supply Chain", "Manufactured Consumer Goods & Electronics", "20,124 TEU", "Container Cargo", "LADEN", "Yantian / Shenzhen, China", "Rotterdam, Netherlands", "2026-09-21 06:00 UTC", 29.90, 32.54, 11.0, 345.0, "Underway using Engine", "2026-09-12 17:35 UTC"),
    ("9778791", "219836000", "MADRID MAERSK", "Ultra Large Container Vessel (ULCV)", "Denmark", 214286, "Maersk Line", "Apple, Samsung & Global Tech Hubs", "Consumer Electronics & High-Value Components", "20,568 TEU", "Container Cargo", "LADEN", "Shanghai, China", "Felixstowe, United Kingdom", "2026-09-13 14:00 UTC", 50.98, 1.40, 15.4, 42.0, "Underway using Engine", "2026-09-12 17:45 UTC"),
    ("9795610", "477174600", "COSCO SHIPPING UNIVERSE", "Ultra Large Container Vessel (ULCV)", "Hong Kong", 198500, "COSCO Shipping", "Industrial Machinery & Auto Parts", "Heavy Machinery, Auto EV Batteries, Hardware", "21,237 TEU", "Container Cargo", "LADEN", "Ningbo, China", "Hamburg, Germany", "2026-09-27 10:00 UTC", 1.45, 103.10, 16.0, 120.0, "Underway using Engine", "2026-09-12 17:15 UTC"),
]


class VesselTracker:
    """Maritime alternative data engine tracking commercial tanker & cargo movements."""

    def __init__(self, vessels: Optional[List[Vessel]] = None):
        self._vessels: Dict[str, Vessel] = {}
        self._by_imo: Dict[str, Vessel] = {}
        self._by_mmsi: Dict[str, Vessel] = {}
        self._by_name: Dict[str, Vessel] = {}
        if vessels:
            for v in vessels:
                v.update_chokepoint_proximity()
                self._index_vessel(v)
        else:
            self._init_institutional_fleet()

    def _index_vessel(self, v: Vessel) -> None:
        """Maintains O(1) indexed lookups by IMO, MMSI, and exact uppercase Name."""
        self._vessels[v.imo] = v
        self._by_imo[v.imo] = v
        if v.mmsi:
            self._by_mmsi[v.mmsi.strip()] = v
        if v.name:
            self._by_name[v.name.strip().upper()] = v

    def _init_institutional_fleet(self) -> None:
        """Initializes curated fleet of active crude tankers, LNG carriers, bulkers, and container vessels."""
        initial_fleet = [Vessel(**dict(zip(_FLEET_FIELDS, r))) for r in _FLEET_RECORDS]

        if _HAS_FASTPATH and _C_CHOKEPOINTS is not None:
            lats = [v.latitude for v in initial_fleet]
            lons = [v.longitude for v in initial_fleet]
            batch_res = fast_batch_fleet_geofence(lats, lons, _C_CHOKEPOINTS, len(_CHOKEPOINTS_LIST))
            if batch_res and len(batch_res) == len(initial_fleet):
                for i, (idx, dist, inside) in enumerate(batch_res):
                    v = initial_fleet[i]
                    v.nearest_chokepoint = _CHOKEPOINTS_LIST[idx].name
                    v.distance_to_chokepoint_nm = dist
                    v.in_chokepoint = inside
                    self._index_vessel(v)
                return

        for v in initial_fleet:
            v.update_chokepoint_proximity()
            self._index_vessel(v)

    def list_vessels(
        self,
        vessel_type: Optional[str] = None,
        company: Optional[str] = None,
        chokepoint: Optional[str] = None,
        laden_status: Optional[str] = None,
        commodity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Vessel]:
        """Queries and filters vessels by type, company (operator/charterer), chokepoint, or cargo."""
        vt = vessel_type.lower() if vessel_type else None
        c_clean = company.lower() if company else None
        cp_clean = chokepoint.lower() if chokepoint else None
        cp_clean_nospace = cp_clean.replace(" ", "") if cp_clean else None
        status_upper = laden_status.upper() if laden_status else None
        cmd_clean = commodity.lower() if commodity else None

        results: List[Vessel] = []
        for v in self._vessels.values():
            if vt:
                matches_type = (
                    vt in v.vessel_type.lower()
                    or vt in v.cargo_category.lower()
                    or (vt == "lng" and ("lng" in v.commodity.lower() or "natural gas" in v.vessel_type.lower()))
                    or (vt == "tanker" and "tanker" in v.vessel_type.lower())
                    or (vt == "bulk" and "bulk" in v.vessel_type.lower())
                    or (vt == "container" and "container" in v.vessel_type.lower())
                )
                if not matches_type:
                    continue
            if c_clean:
                if c_clean not in v.operator.lower() and c_clean not in v.charterer.lower():
                    continue
            if cp_clean:
                cp_lower = v.nearest_chokepoint.lower()
                if cp_clean not in cp_lower and cp_clean_nospace not in cp_lower.replace(" ", ""):
                    continue
            if status_upper and status_upper != v.laden_status.upper():
                continue
            if cmd_clean:
                if cmd_clean not in v.commodity.lower() and cmd_clean not in v.cargo_category.lower():
                    continue

            results.append(v)
            if len(results) >= limit:
                break

        return results

    def get_vessel(self, identifier: str) -> Optional[Vessel]:
        """Looks up a vessel by IMO number, MMSI, or Name (O(1) exact, fallback to substring)."""
        if not identifier:
            return None
        clean_id = sanitize_vessel_string(identifier).upper()
        if clean_id in self._by_imo:
            return self._by_imo[clean_id]
        if clean_id in self._by_mmsi:
            return self._by_mmsi[clean_id]
        if clean_id in self._by_name:
            return self._by_name[clean_id]

        for v in self._vessels.values():
            if clean_id in v.name.upper():
                return v
        return None

    def get_chokepoint_traffic(self, max_distance_nm: float = 150.0) -> Dict[str, List[Vessel]]:
        """Groups all vessels approaching or within critical maritime bottlenecks."""
        traffic: Dict[str, List[Vessel]] = {cp.name: [] for cp in GLOBAL_CHOKEPOINTS.values()}
        for v in self._vessels.values():
            if v.nearest_chokepoint in traffic and v.distance_to_chokepoint_nm <= max_distance_nm:
                traffic[v.nearest_chokepoint].append(v)
        return traffic

    def get_commodity_breakdown(self) -> Dict[str, Any]:
        """Calculates institutional alternative data breakdown of commodities currently floating at sea."""
        categories: Dict[str, Dict[str, Any]] = {}
        charterers: Dict[str, int] = {}
        operators: Dict[str, int] = {}

        total_vessels = len(self._vessels)
        laden_vessels = 0

        for v in self._vessels.values():
            cat = v.cargo_category
            if cat not in categories:
                categories[cat] = {
                    "vessel_count": 0,
                    "total_dwt": 0,
                    "commodities": set(),
                    "charterers": set(),
                }
            categories[cat]["vessel_count"] += 1
            categories[cat]["total_dwt"] += v.dwt
            categories[cat]["commodities"].add(v.commodity)
            categories[cat]["charterers"].add(v.charterer)

            if v.laden_status == "LADEN":
                laden_vessels += 1

            charterers[v.charterer] = charterers.get(v.charterer, 0) + 1
            operators[v.operator] = operators.get(v.operator, 0) + 1

        # Convert sets to sorted lists for serialization
        for c in categories.values():
            c["commodities"] = sorted(c["commodities"])
            c["charterers"] = sorted(c["charterers"])

        return {
            "total_vessels_tracked": total_vessels,
            "laden_vessels": laden_vessels,
            "ballast_vessels": total_vessels - laden_vessels,
            "categories": categories,
            "top_charterers": sorted(charterers.items(), key=lambda x: x[1], reverse=True),
            "top_operators": sorted(operators.items(), key=lambda x: x[1], reverse=True),
        }

    def update_position(
        self,
        imo: str,
        lat: float,
        lon: float,
        speed: Optional[float] = None,
        heading: Optional[float] = None,
    ) -> Optional[Vessel]:
        """Updates real-time GPS coordinates of a tracked ship with strict validation."""
        valid_lat, valid_lon = validate_coordinates(lat, lon)
        valid_speed, valid_heading = validate_speed_and_heading(speed, heading)

        v = self.get_vessel(imo)
        if not v:
            return None
        v.latitude = valid_lat
        v.longitude = valid_lon
        if valid_speed is not None:
            v.speed_knots = valid_speed
        if valid_heading is not None:
            v.heading = valid_heading
        v.last_update = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        v.update_chokepoint_proximity()
        return v
