"""
Tests for the Fuel Route Planner.

Run with:
    python manage.py test route_planner
"""
import math
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient


class TestHaversine(TestCase):
    def test_same_point(self):
        from route_planner.fuel_data import haversine_miles
        self.assertAlmostEqual(haversine_miles(34.0, -118.0, 34.0, -118.0), 0.0, places=2)

    def test_la_to_las_vegas(self):
        from route_planner.fuel_data import haversine_miles
        d = haversine_miles(34.052, -118.243, 36.175, -115.136)
        self.assertGreater(d, 200)
        self.assertLess(d, 260)


class TestPolylineDecoder(TestCase):
    def test_known_polyline(self):
        from route_planner.osrm_service import decode_polyline
        encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        result = decode_polyline(encoded)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0][0], 38.5, places=1)
        self.assertAlmostEqual(result[0][1], -120.2, places=1)


class TestOptimizer(TestCase):
    def _make_route(self, total_miles=700):
        waypoints = [
            {"lat": 34.0 + i * 0.5, "lon": -118.0 + i * 0.5,
             "distance_along_route_miles": i * 50}
            for i in range(1, int(total_miles // 50) + 1)
        ]
        return {"total_distance_miles": total_miles, "waypoints_every_50mi": waypoints}

    @patch("route_planner.optimizer._find_stations_for_window")
    def test_no_stop_needed_under_500mi(self, mock_find):
        from route_planner.optimizer import plan_fuel_stops
        route = self._make_route(total_miles=400)
        result = plan_fuel_stops(route, (34.0, -118.0), (36.0, -116.0))
        self.assertEqual(result["stops"], [])

    @patch("route_planner.optimizer._find_stations_for_window")
    def test_one_stop_needed_600mi(self, mock_find):
        from route_planner.optimizer import plan_fuel_stops
        fake = {"id": "1", "name": "Test Station", "address": "I-40",
                "city": "Flagstaff", "state": "AZ", "lat": 35.2, "lon": -111.6,
                "price": 3.20, "approx_route_miles": 300}
        mock_find.return_value = [fake]
        route = self._make_route(total_miles=600)
        result = plan_fuel_stops(route, (34.0, -118.0), (39.0, -113.0))
        self.assertEqual(len(result["stops"]), 1)
        self.assertEqual(result["stops"][0]["station_name"], "Test Station")

    @patch("route_planner.optimizer._find_stations_for_window")
    def test_cheapest_station_selected(self, mock_find):
        from route_planner.optimizer import plan_fuel_stops
        cheap = {"id": "2", "name": "Cheap Gas", "address": "I-40",
                 "city": "Kingman", "state": "AZ", "lat": 35.2, "lon": -114.0,
                 "price": 2.95, "approx_route_miles": 280}
        expensive = {"id": "3", "name": "Pricey Gas", "address": "I-40",
                     "city": "Barstow", "state": "CA", "lat": 34.9, "lon": -117.0,
                     "price": 4.10, "approx_route_miles": 100}
        mock_find.return_value = [cheap, expensive]
        route = self._make_route(total_miles=600)
        result = plan_fuel_stops(route, (34.0, -118.0), (39.0, -113.0))
        self.assertEqual(result["stops"][0]["station_name"], "Cheap Gas")

    @patch("route_planner.optimizer._find_stations_for_window")
    def test_no_stations_raises_error(self, mock_find):
        from route_planner.optimizer import plan_fuel_stops
        mock_find.return_value = []
        route = self._make_route(total_miles=600)
        with self.assertRaises(ValueError):
            plan_fuel_stops(route, (34.0, -118.0), (39.0, -113.0))

    @patch("route_planner.optimizer._find_stations_for_window")
    def test_fuel_cost_calculation(self, mock_find):
        from route_planner.optimizer import plan_fuel_stops
        station = {"id": "4", "name": "Mid Station", "address": "I-10",
                   "city": "Phoenix", "state": "AZ", "lat": 33.4, "lon": -112.0,
                   "price": 3.00, "approx_route_miles": 400}
        mock_find.return_value = [station]
        route = self._make_route(total_miles=800)
        result = plan_fuel_stops(route, (34.0, -118.0), (40.0, -108.0))
        stop = result["stops"][0]
        self.assertAlmostEqual(stop["gallons_purchased"], 40.0, places=1)
        self.assertAlmostEqual(stop["cost_at_stop"], 120.0, places=1)


class TestRouteAPI(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_missing_fields(self):
        response = self.client.post("/api/route/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_missing_finish(self):
        response = self.client.post("/api/route/", {"start": "Los Angeles, CA"}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("route_planner.views.geocode_location")
    @patch("route_planner.views.get_route")
    @patch("route_planner.views.plan_fuel_stops")
    def test_successful_short_trip(self, mock_plan, mock_route, mock_geocode):
        mock_geocode.side_effect = [(34.052, -118.243), (36.175, -115.136)]
        mock_route.return_value = {
            "total_distance_miles": 270.3, "duration_seconds": 14400,
            "geometry": [[34.0, -118.0], [36.0, -115.0]],
            "waypoints_every_50mi": [],
            "bbox": [34.0, -118.2, 36.2, -115.1],
        }
        mock_plan.return_value = {
            "stops": [], "total_distance_miles": 270.3,
            "total_gallons": 27.03, "total_fuel_cost": 0.0,
        }
        response = self.client.post(
            "/api/route/",
            {"start": "Los Angeles, CA", "finish": "Las Vegas, NV"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("route", response.data)
        self.assertIn("fuel_plan", response.data)
        self.assertIn("geometry", response.data["route"])
        self.assertEqual(response.data["meta"]["vehicle_mpg"], 10)

    @patch("route_planner.views.geocode_location")
    def test_invalid_location(self, mock_geocode):
        mock_geocode.side_effect = ValueError("Could not geocode location: 'Atlantis'")
        response = self.client.post(
            "/api/route/", {"start": "Atlantis", "finish": "New York"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    @patch("route_planner.views.geocode_location")
    @patch("route_planner.views.get_route")
    def test_osrm_failure(self, mock_route, mock_geocode):
        mock_geocode.side_effect = [(34.0, -118.0), (36.0, -115.0)]
        mock_route.side_effect = Exception("OSRM unreachable")
        response = self.client.post(
            "/api/route/", {"start": "Los Angeles, CA", "finish": "Las Vegas, NV"}, format="json"
        )
        self.assertEqual(response.status_code, 502)

    def test_health_endpoint(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")


class TestFuelData(TestCase):
    def test_stations_loaded(self):
        from route_planner.fuel_data import STATIONS
        self.assertGreater(len(STATIONS), 1000)

    def test_all_prices_positive(self):
        from route_planner.fuel_data import STATIONS
        for s in STATIONS:
            self.assertGreater(s["price"], 0)

    def test_state_index_populated(self):
        from route_planner.fuel_data import _STATE_INDEX
        self.assertIn("TX", _STATE_INDEX)
        self.assertIn("CA", _STATE_INDEX)
