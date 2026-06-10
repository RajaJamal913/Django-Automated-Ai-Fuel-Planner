"""
OSRM routing service.

Makes ONE call to the OSRM route API to get:
  - Total distance in miles
  - Full geometry (polyline) for the map
  - Waypoints sampled every ~50 miles for fuel station search

Free APIs used (no key required):
  - Nominatim (OpenStreetMap) for geocoding
  - OSRM demo server for routing
"""
import math
import time
from typing import Any

import requests
from django.conf import settings

OSRM_BASE = getattr(settings, "OSRM_BASE_URL", "https://router.project-osrm.org")

# ── Fallback city coordinates (avoids Nominatim for common US cities) ──────────
# Covers the most-queried US cities so the API responds instantly for typical inputs.
_CITY_COORDS: dict[str, tuple[float, float]] = {
    # Format: "city, state abbreviation" (lowercase)
    "new york, ny": (40.7128, -74.0060),
    "new york city, ny": (40.7128, -74.0060),
    "nyc, ny": (40.7128, -74.0060),
    "los angeles, ca": (34.0522, -118.2437),
    "la, ca": (34.0522, -118.2437),
    "chicago, il": (41.8781, -87.6298),
    "houston, tx": (29.7604, -95.3698),
    "phoenix, az": (33.4484, -112.0740),
    "philadelphia, pa": (39.9526, -75.1652),
    "san antonio, tx": (29.4241, -98.4936),
    "san diego, ca": (32.7157, -117.1611),
    "dallas, tx": (32.7767, -96.7970),
    "san jose, ca": (37.3382, -121.8863),
    "san francisco, ca": (37.7749, -122.4194),
    "sf, ca": (37.7749, -122.4194),
    "austin, tx": (30.2672, -97.7431),
    "jacksonville, fl": (30.3322, -81.6557),
    "fort worth, tx": (32.7555, -97.3308),
    "columbus, oh": (39.9612, -82.9988),
    "charlotte, nc": (35.2271, -80.8431),
    "indianapolis, in": (39.7684, -86.1581),
    "san francisco, ca": (37.7749, -122.4194),
    "seattle, wa": (47.6062, -122.3321),
    "denver, co": (39.7392, -104.9903),
    "washington, dc": (38.9072, -77.0369),
    "nashville, tn": (36.1627, -86.7816),
    "oklahoma city, ok": (35.4676, -97.5164),
    "el paso, tx": (31.7619, -106.4850),
    "boston, ma": (42.3601, -71.0589),
    "portland, or": (45.5231, -122.6765),
    "las vegas, nv": (36.1699, -115.1398),
    "memphis, tn": (35.1495, -90.0490),
    "louisville, ky": (38.2527, -85.7585),
    "baltimore, md": (39.2904, -76.6122),
    "milwaukee, wi": (43.0389, -87.9065),
    "albuquerque, nm": (35.0844, -106.6504),
    "tucson, az": (32.2226, -110.9747),
    "fresno, ca": (36.7378, -119.7871),
    "sacramento, ca": (38.5816, -121.4944),
    "mesa, az": (33.4152, -111.8315),
    "kansas city, mo": (39.0997, -94.5786),
    "atlanta, ga": (33.7490, -84.3880),
    "omaha, ne": (41.2565, -95.9345),
    "colorado springs, co": (38.8339, -104.8214),
    "raleigh, nc": (35.7796, -78.6382),
    "long beach, ca": (33.7701, -118.1937),
    "virginia beach, va": (36.8529, -75.9780),
    "minneapolis, mn": (44.9778, -93.2650),
    "tampa, fl": (27.9506, -82.4572),
    "miami, fl": (25.7617, -80.1918),
    "new orleans, la": (29.9511, -90.0715),
    "cleveland, oh": (41.4993, -81.6944),
    "pittsburgh, pa": (40.4406, -79.9959),
    "detroit, mi": (42.3314, -83.0458),
    "st. louis, mo": (38.6270, -90.1994),
    "st louis, mo": (38.6270, -90.1994),
    "cincinnati, oh": (39.1031, -84.5120),
    "salt lake city, ut": (40.7608, -111.8910),
    "boise, id": (43.6150, -116.2023),
    "spokane, wa": (47.6588, -117.4260),
    "richmond, va": (37.5407, -77.4360),
    "buffalo, ny": (42.8864, -78.8784),
    "hartford, ct": (41.7658, -72.6851),
    "providence, ri": (41.8240, -71.4128),
    "honolulu, hi": (21.3069, -157.8583),
    "anchorage, ak": (61.2181, -149.9003),
    "bismarck, nd": (46.8083, -100.7837),
    "cheyenne, wy": (41.1400, -104.8202),
    "helena, mt": (46.5958, -112.0270),
    "jackson, ms": (32.2988, -90.1848),
    "little rock, ar": (34.7465, -92.2896),
    "birmingham, al": (33.5186, -86.8104),
    "montgomery, al": (32.3617, -86.2792),
    "charleston, wv": (38.3498, -81.6326),
    "columbia, sc": (34.0007, -81.0348),
    "concord, nh": (43.2081, -71.5376),
    "montpelier, vt": (44.2601, -72.5754),
    "augusta, me": (44.3106, -69.7795),
    "bangor, me": (44.8012, -68.7778),
    "portland, me": (43.6591, -70.2568),
}


def _lookup_fallback(place: str) -> tuple[float, float] | None:
    """Check the built-in city table before hitting Nominatim."""
    key = place.strip().lower()
    # Try exact match
    if key in _CITY_COORDS:
        return _CITY_COORDS[key]
    # Try stripping "usa" / "united states" suffix
    for suffix in (", usa", ", united states", ", us"):
        if key.endswith(suffix):
            trimmed = key[: -len(suffix)].strip()
            if trimmed in _CITY_COORDS:
                return _CITY_COORDS[trimmed]
    return None


def geocode_location(place: str) -> tuple[float, float]:
    """
    Geocode a US place name → (lat, lon).

    First checks a built-in table of common US cities (instant, no network).
    Falls back to Nominatim if not found.
    """
    # Fast path: built-in lookup
    result = _lookup_fallback(place)
    if result:
        return result

    # Slow path: Nominatim
    headers = {"User-Agent": "fuel-route-planner/1.0 (assessment)"}
    params = {
        "q": f"{place}, USA",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        raise ValueError(
            f"Could not geocode '{place}'. "
            f"Try a format like 'Chicago, IL' or 'Los Angeles, CA'. "
            f"(Detail: {e})"
        )

    raise ValueError(
        f"Could not geocode location: '{place}'. "
        "Try a format like 'Chicago, IL' or 'Los Angeles, CA'."
    )


# ── Polyline decoder ──────────────────────────────────────────────────────────

def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode a Google-format encoded polyline → list of (lat, lon)."""
    coords = []
    index = lat = lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
                coords.append((lat / 1e5, lng / 1e5))
            else:
                lat += delta
    return coords


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cumulative_distances(coords: list[tuple[float, float]]) -> list[float]:
    cum = [0.0]
    for i in range(1, len(coords)):
        cum.append(cum[-1] + haversine_miles(*coords[i - 1], *coords[i]))
    return cum


def sample_waypoints_every_n_miles(
    coords: list[tuple[float, float]],
    cum_dist: list[float],
    interval_miles: float = 50,
) -> list[dict]:
    total = cum_dist[-1]
    waypoints = []
    target = interval_miles
    while target < total:
        lo, hi = 0, len(cum_dist) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cum_dist[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        waypoints.append({
            "lat": coords[lo][0],
            "lon": coords[lo][1],
            "distance_along_route_miles": round(cum_dist[lo], 1),
        })
        target += interval_miles
    return waypoints


# ── Main entry point ──────────────────────────────────────────────────────────

def get_route(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
) -> dict[str, Any]:
    """
    Single OSRM call. Returns route distance, geometry, and sampled waypoints.
    """
    url = (
        f"{OSRM_BASE}/route/v1/driving/"
        f"{start_lon},{start_lat};{end_lon},{end_lat}"
    )
    params = {"overview": "full", "geometries": "polyline", "steps": "false"}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"OSRM returned no route: {data.get('message', data.get('code'))}")

    route = data["routes"][0]
    distance_miles = route["distance"] / 1609.344
    duration_seconds = route["duration"]

    coords = decode_polyline(route["geometry"])
    cum_dist = _cumulative_distances(coords)
    waypoints = sample_waypoints_every_n_miles(coords, cum_dist, interval_miles=50)

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]

    return {
        "total_distance_miles": round(distance_miles, 1),
        "duration_seconds": round(duration_seconds),
        "geometry": [[c[0], c[1]] for c in coords],
        "waypoints_every_50mi": waypoints,
        "bbox": [min(lats), min(lons), max(lats), max(lons)],
    }