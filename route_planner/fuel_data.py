"""
Fuel station data loader.

City coordinates are resolved from the bundled `zipcodes` package (ships
with its own data, no network calls). A city_coords.csv cache is written
next to this file on first run so subsequent starts are instant.
"""
import csv
import math
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
CITY_COORDS_PATH = BASE / "city_coords.csv"

US_STATES = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN',
    'IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV',
    'NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN',
    'TX','UT','VT','VA','WA','WV','WI','WY','DC',
}


# ── City coordinates ──────────────────────────────────────────────────────────

def _build_city_coords() -> dict[tuple[str, str], tuple[float, float]]:
    """Build city→coords from the bundled zipcodes package and cache to disk."""
    import zipcodes as zc

    city_pts: dict[tuple[str, str], list] = defaultdict(list)
    for z in zc.list_all():
        if z.get("country") == "US" and z.get("lat") and z.get("long"):
            key = (z["city"].upper().strip(), z["state"].upper().strip())
            city_pts[key].append((float(z["lat"]), float(z["long"])))

    coords = {
        k: (round(sum(p[0] for p in pts) / len(pts), 4),
            round(sum(p[1] for p in pts) / len(pts), 4))
        for k, pts in city_pts.items()
    }

    # Write cache so next startup is instant
    with open(CITY_COORDS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["city", "state", "lat", "lon"])
        for (city, state), (lat, lon) in sorted(coords.items()):
            writer.writerow([city, state, lat, lon])

    return coords


def _load_city_coords() -> dict[tuple[str, str], tuple[float, float]]:
    """Load from cache if available, otherwise build it."""
    if CITY_COORDS_PATH.exists():
        coords = {}
        with open(CITY_COORDS_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["city"].upper().strip(), row["state"].upper().strip())
                coords[key] = (float(row["lat"]), float(row["lon"]))
        return coords
    return _build_city_coords()


_CITY_COORDS = _load_city_coords()


def _geocode(city: str, state: str) -> tuple[float, float] | None:
    return _CITY_COORDS.get((city.upper().strip(), state.upper().strip()))


# ── Load fuel stations ────────────────────────────────────────────────────────

def _load_stations() -> list[dict]:
    stations = []
    with open(BASE / "fuel_prices.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            state = row.get("State", "").strip().upper()
            if state not in US_STATES:
                continue
            try:
                price = float(row["Retail Price"])
            except (ValueError, KeyError):
                continue
            city = row.get("City", "").strip()
            coords = _geocode(city, state)
            stations.append({
                "id": row.get("OPIS Truckstop ID", ""),
                "name": row.get("Truckstop Name", "").strip(),
                "address": row.get("Address", "").strip(),
                "city": city,
                "state": state,
                "price": price,
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
            })
    return stations


STATIONS = _load_stations()
GEOCODED_STATIONS = [s for s in STATIONS if s["lat"] is not None]

_STATE_INDEX: dict[str, list] = {}
for _s in GEOCODED_STATIONS:
    _STATE_INDEX.setdefault(_s["state"], []).append(_s)


# ── Haversine distance ────────────────────────────────────────────────────────

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Station search ────────────────────────────────────────────────────────────

def stations_near_point(
    lat: float,
    lon: float,
    radius_miles: float = 15,
    states_hint: set[str] | None = None,
) -> list[dict]:
    """Return stations within radius_miles of (lat, lon) — fully offline."""
    pool = []
    if states_hint:
        for st in states_hint:
            pool.extend(_STATE_INDEX.get(st, []))
    else:
        pool = GEOCODED_STATIONS

    results = []
    for s in pool:
        d = haversine_miles(lat, lon, s["lat"], s["lon"])
        if d <= radius_miles:
            results.append({**s, "distance_from_point": round(d, 2)})
    return results