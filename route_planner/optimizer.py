"""
Fuel stop optimizer.

Given a route and sampled waypoints, determines:
  1. WHERE refuel stops are needed (max range 500 miles, start full)
  2. WHICH station — cheapest among reachable stations ahead of current position

Algorithm: greedy "cheapest in full range window"
  - Look ahead the entire MAX_RANGE from current position
  - Pick the cheapest station reachable in that window
  - Advance past that station to avoid revisiting it
"""
from __future__ import annotations
from typing import Any

from .fuel_data import stations_near_point, haversine_miles

# Constants defined here — no Django settings import at module level
MAX_RANGE = 500   # miles
MPG = 10
SEARCH_RADII = [15, 30, 60]


def _find_stations_for_window(
    waypoints: list[dict],
    window_start: float,
    window_end: float,
    radius_miles: float = 15,
) -> list[dict]:
    """Find all stations near waypoints strictly inside (window_start, window_end]."""
    window_wps = [
        w for w in waypoints
        if window_start < w["distance_along_route_miles"] <= window_end
    ]
    if not window_wps:
        return []

    seen: set[tuple] = set()
    results: list[dict] = []
    for wp in window_wps:
        for s in stations_near_point(wp["lat"], wp["lon"], radius_miles=radius_miles):
            key = (s["id"], s["name"])
            if key not in seen:
                seen.add(key)
                results.append({**s, "approx_route_miles": wp["distance_along_route_miles"]})
    return results


def _find_best_station(
    waypoints: list[dict],
    window_start: float,
    window_end: float,
) -> dict | None:
    """Try progressively wider search radii; return cheapest station or None."""
    for radius in SEARCH_RADII:
        candidates = _find_stations_for_window(waypoints, window_start, window_end, radius)
        if candidates:
            return min(candidates, key=lambda s: s["price"])
    return None


def plan_fuel_stops(
    route: dict[str, Any],
    start_coords: tuple[float, float],
    end_coords: tuple[float, float],
) -> dict[str, Any]:
    """
    Plan optimal (cheapest) fuel stops for the route.
    Returns stop list, total gallons, and total fuel cost.
    """
    total_miles = route["total_distance_miles"]
    waypoints = route["waypoints_every_50mi"]

    stops: list[dict] = []
    prev_stop_miles = 0.0
    current_miles = 0.0
    stop_number = 1

    while True:
        remaining = total_miles - current_miles
        if remaining <= MAX_RANGE:
            break  # can reach destination on current tank

        window_start = current_miles
        window_end = min(current_miles + MAX_RANGE, total_miles)

        best = _find_best_station(waypoints, window_start, window_end)

        if best is None:
            raise ValueError(
                f"No fuel stations found between miles {window_start:.0f} "
                f"and {window_end:.0f} along the route "
                f"(searched up to {SEARCH_RADII[-1]} miles off route)."
            )

        stop_at_miles = best["approx_route_miles"]

        if stop_at_miles <= current_miles:
            raise ValueError(
                f"Optimizer stalled: best station at {stop_at_miles:.0f}mi "
                f"is not ahead of current position {current_miles:.0f}mi."
            )

        leg_miles = stop_at_miles - prev_stop_miles
        gallons = leg_miles / MPG

        stops.append({
            "stop_number": stop_number,
            "station_name": best["name"],
            "address": best["address"],
            "city": best["city"],
            "state": best["state"],
            "lat": best["lat"],
            "lon": best["lon"],
            "price_per_gallon": round(best["price"], 3),
            "gallons_purchased": round(gallons, 2),
            "cost_at_stop": round(gallons * best["price"], 2),
            "distance_from_last_stop_miles": round(leg_miles, 1),
            "cumulative_route_miles": round(stop_at_miles, 1),
        })

        prev_stop_miles = stop_at_miles
        current_miles = stop_at_miles
        stop_number += 1

    # Final leg: fuel bought at last stop covers the rest
    final_leg_miles = total_miles - prev_stop_miles
    final_gallons = round(final_leg_miles / MPG, 2)
    final_price = stops[-1]["price_per_gallon"] if stops else 0.0

    total_gallons = round(sum(s["gallons_purchased"] for s in stops) + final_gallons, 2)
    total_cost = round(sum(s["cost_at_stop"] for s in stops) + final_gallons * final_price, 2)

    return {
        "stops": stops,
        "total_distance_miles": total_miles,
        "total_gallons": total_gallons,
        "total_fuel_cost": total_cost,
    }