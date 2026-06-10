import time

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .osrm_service import geocode_location, get_route
from .optimizer import plan_fuel_stops


DEFAULT_FUEL_PRICE = 3.50  # fallback for consumption cost


def build_route_geojson(route: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            # OSRM gives (lat, lon) → GeoJSON needs (lon, lat)
            "coordinates": [[lon, lat] for lat, lon in route["geometry"]],
        },
        "properties": {
            "distance_miles": route["total_distance_miles"],
            "duration_hours": round(route["duration_seconds"] / 3600, 1),
        },
    }


def build_stops_geojson(fuel_plan: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [s["lon"], s["lat"]],
                },
                "properties": {
                    "stop_number": s["stop_number"],
                    "name": s["station_name"],
                    "address": s["address"],
                    "city": s["city"],
                    "state": s["state"],
                    "price_per_gallon": s["price_per_gallon"],
                    "gallons_purchased": s["gallons_purchased"],
                    "cost_at_stop": s["cost_at_stop"],
                    "cumulative_route_miles": s["cumulative_route_miles"],
                },
            }
            for s in fuel_plan["stops"]
        ],
    }


@api_view(["POST"])
def plan_route(request):
    """
    Plan a fuel-optimal road trip route between two US locations.

    Request body:
        {"start": "Los Angeles, CA", "finish": "Chicago, IL"}
    """

    start = (request.data.get("start") or "").strip()
    finish = (request.data.get("finish") or "").strip()

    if not start or not finish:
        return Response(
            {"error": "Both 'start' and 'finish' fields are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    t0 = time.perf_counter()

    try:
        # -------------------------
        # 1. Geocode locations
        # -------------------------
        try:
            start_lat, start_lon = geocode_location(start)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            end_lat, end_lon = geocode_location(finish)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # -------------------------
        # 2. Get route from OSRM
        # -------------------------
        try:
            route = get_route(start_lat, start_lon, end_lat, end_lon)
        except Exception as e:
            return Response(
                {"error": f"Routing failed: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # -------------------------
        # 3. Compute fuel stops
        # -------------------------
        try:
            fuel_plan = plan_fuel_stops(
                route,
                (start_lat, start_lon),
                (end_lat, end_lon),
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # -------------------------
        # 4. Build map layers
        # -------------------------
        route_geojson = build_route_geojson(route)
        stops_geojson = build_stops_geojson(fuel_plan)

        # -------------------------
        # 5. Fuel cost correction
        # -------------------------
        total_gallons = fuel_plan["total_gallons"]

        estimated_total_fuel_cost = round(
            total_gallons * DEFAULT_FUEL_PRICE, 2
        )

        # -------------------------
        # 6. Response
        # -------------------------
        elapsed = round(time.perf_counter() - t0, 2)

        return Response({
            "start": start,
            "finish": finish,

            "start_coords": {
                "lat": start_lat,
                "lon": start_lon,
            },
            "end_coords": {
                "lat": end_lat,
                "lon": end_lon,
            },

            # ---------------- MAP DATA ----------------
            "route": {
                "summary": {
                    "total_distance_miles": route["total_distance_miles"],
                    "duration_hours": round(route["duration_seconds"] / 3600, 1),
                },
                "geojson": route_geojson,
                "bbox": route["bbox"],
            },

            "fuel": {
                **fuel_plan,
                "estimated_total_fuel_cost": estimated_total_fuel_cost,
            },

            "stops_map": stops_geojson,

            "meta": {
                "elapsed_seconds": elapsed,
                "vehicle_mpg": 10,
                "vehicle_max_range_miles": 500,
                "fuel_price_assumption": DEFAULT_FUEL_PRICE,
            },
        })

    except Exception as e:
        return Response(
            {"error": f"Unexpected error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})