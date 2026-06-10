# Fuel Route Planner API

A Django REST API that plans fuel-optimal road trips between two US locations, using real fuel price data to minimise cost.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run migrations (only needed for Django internals, no app models)
python manage.py migrate

# 3. Start the server
python manage.py runserver

# Or with gunicorn for production
gunicorn fuel_route_project.wsgi:application --bind 0.0.0.0:8000
```

---

## API Reference

### `POST /api/route/`

Plan a fuel-optimal route between two US locations.

**Request body**
```json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY"
}
```

**Response**
```json
{
  "start": "Los Angeles, CA",
  "finish": "New York, NY",
  "start_coords": { "lat": 34.052, "lon": -118.243 },
  "end_coords":   { "lat": 40.712, "lon": -74.005 },
  "route": {
    "total_distance_miles": 2790.4,
    "estimated_drive_time_hours": 39.2,
    "geometry": [[34.052, -118.243], ...],   // full polyline for map rendering
    "bbox": [34.0, -118.3, 40.8, -74.0]     // [min_lat, min_lon, max_lat, max_lon]
  },
  "fuel_plan": {
    "total_distance_miles": 2790.4,
    "total_gallons": 279.0,
    "total_fuel_cost": 894.23,
    "stops": [
      {
        "stop_number": 1,
        "station_name": "PILOT TRAVEL CENTER #1243",
        "address": "I-40, EXIT 22",
        "city": "Needles",
        "state": "CA",
        "lat": 34.848,
        "lon": -114.614,
        "price_per_gallon": 3.199,
        "gallons_purchased": 45.0,
        "cost_at_stop": 143.96,
        "distance_from_last_stop_miles": 152.3,
        "cumulative_route_miles": 152.3
      }
      // ... more stops
    ]
  },
  "meta": {
    "elapsed_seconds": 5.2,
    "vehicle_mpg": 10,
    "vehicle_max_range_miles": 500
  }
}
```

### `GET /api/health/`

Returns `{"status": "ok"}`.

---

## Architecture & Design Decisions

### External API calls: minimised by design

The assignment asks for as few external API calls as possible — **one is ideal, two or three acceptable**. This implementation makes exactly **three** external calls total:

| Call | Service | Purpose |
|------|---------|---------|
| 1 | Nominatim (OSM) | Geocode start location |
| 2 | Nominatim (OSM) | Geocode finish location |
| 3 | OSRM | Get full route geometry + distance |

The **fuel station search is entirely local** — no external calls. The 8,151 stations from the provided CSV are loaded into memory at startup, geocoded lazily (by city/state via Nominatim, with caching), and searched with haversine distance calculations.

### Free APIs used

- **OSRM** (`router.project-osrm.org`) — open-source routing engine, no API key, no usage limits for reasonable traffic. Drop-in replaceable with a self-hosted instance for production.
- **Nominatim** (`nominatim.openstreetmap.org`) — free geocoding from OpenStreetMap, no key required. Rate limited to 1 request/second (respected in code).

### Fuel stop optimisation algorithm

The optimizer uses a greedy "cheapest in window" approach:

1. Start at mile 0 with a full tank (500-mile range).
2. Identify the reachable window: `[current_position, current_position + 500 miles]`.
3. Find all stations within 15 miles of any route waypoint in that window.
4. Select the **cheapest** station.
5. Advance to that station, repeat until the destination is reachable without another stop.

This is O(N × S) where N is the number of 50-mile waypoints in a window and S is the number of nearby stations. It runs in milliseconds for typical US routes.

### Performance

- **CSV loaded once at startup** into a Python list (~8k stations, ~5 MB in memory).
- **State-indexed** so candidate pools are pre-filtered by US state before haversine computation.
- **Geocode cache** (thread-safe dict) means each unique city/state is geocoded at most once per process lifetime; a warm cache serves requests in ~1–2 seconds (dominated by the two Nominatim + one OSRM calls).
- **Nominatim rate limit** (1.1s sleeps between calls) means cold-start requests for new city/state combinations add ~1s per unique city. A production deployment should pre-geocode all 8k stations offline and store results in the DB.

### Production hardening (not in scope but noted)

- Pre-geocode all stations and store `lat`/`lon` in SQLite/PostGIS.
- Add Redis caching for route responses (same O/D pair → instant repeat).
- Self-host OSRM for SLA guarantees.
- Replace `SECRET_KEY` and set `DEBUG=False` via environment variables.

---

## Running Tests

```bash
python manage.py test route_planner --verbosity=2
```

17 tests covering:
- Haversine distance calculation
- Google-format polyline decoder
- Optimizer logic (no stop needed, one stop, cheapest selection, cost calculation, error handling)
- API endpoint (validation, success, OSRM failure, invalid location)
- Fuel data loading and state index

---

## Project Structure

```
fuel_route/
├── fuel_route_project/
│   ├── settings.py          # Django settings
│   ├── urls.py              # Root URL conf
│   └── wsgi.py
├── route_planner/
│   ├── fuel_data.py         # CSV loader, geocoding, haversine, station search
│   ├── osrm_service.py      # OSRM API client, polyline decoder, waypoint sampler
│   ├── optimizer.py         # Greedy fuel stop planner
│   ├── views.py             # DRF API views
│   ├── urls.py              # App URL conf
│   ├── tests.py             # 17 unit + integration tests
│   └── fuel_prices.csv      # Source data (8,151 US fuel stations)
├── requirements.txt
└── README.md
```
