"""Unit tests for the Pydantic v2 API models.

Covers task 3.12: "Pydantic model validation edge cases".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from open_routing_service.models.api import (
    ClosestFacilityRequest,
    ClosestFacilityResponse,
    ErrorResponse,
    FacilityResult,
    IncidentPoint,
    RouteGeoJSON,
)


class TestIncidentPoint:
    def test_valid_point(self) -> None:
        p = IncidentPoint(lat=32.71, lon=-117.16)
        assert p.lat == 32.71

    @pytest.mark.parametrize("lat", [-90.1, 90.1, 91.0])
    def test_invalid_latitude(self, lat: float) -> None:
        with pytest.raises(ValidationError):
            IncidentPoint(lat=lat, lon=0.0)

    @pytest.mark.parametrize("lon", [-180.1, 180.1, 200.0])
    def test_invalid_longitude(self, lon: float) -> None:
        with pytest.raises(ValidationError):
            IncidentPoint(lat=0.0, lon=lon)

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IncidentPoint(lat=0.0, lon=0.0, elevation=1.0)  # type: ignore[call-arg]


class TestClosestFacilityRequest:
    def _base(self, **over: object) -> dict[str, object]:
        d: dict[str, object] = {
            "incident": {"lat": 32.71, "lon": -117.16},
            "buffer_m": 500.0,
            "k": 3,
            "cost_mode": "distance",
            "facility_filter": {"amenity": "fire_station"},
        }
        d.update(over)
        return d

    def test_valid_request(self) -> None:
        req = ClosestFacilityRequest.model_validate(self._base())
        assert req.k == 3
        assert req.facility_filter == {"amenity": "fire_station"}

    def test_defaults(self) -> None:
        req = ClosestFacilityRequest.model_validate(
            {"incident": {"lat": 0.0, "lon": 0.0}}
        )
        assert req.buffer_m == 152.4
        assert req.k == 1
        assert req.cost_mode == "distance"
        assert req.facility_filter == {}

    @pytest.mark.parametrize("k", [0, -1, 11, 100])
    def test_k_out_of_range(self, k: int) -> None:
        with pytest.raises(ValidationError):
            ClosestFacilityRequest.model_validate(self._base(k=k))

    @pytest.mark.parametrize(
        "buffer_m",
        # Below the 10 m floor, and above the 10-mile (16 093.44 m) cap.
        [0.0, 9.99, -1.0, 16_093.45, 20_000.0, 50_001.0],
    )
    def test_buffer_out_of_range(self, buffer_m: float) -> None:
        with pytest.raises(ValidationError):
            ClosestFacilityRequest.model_validate(self._base(buffer_m=buffer_m))

    @pytest.mark.parametrize("buffer_m", [10.0, 152.4, 16_093.44])
    def test_buffer_within_range_accepted(self, buffer_m: float) -> None:
        # 10-mile cap = 16 093.44 m (scalable-routing-etl design D8).
        req = ClosestFacilityRequest.model_validate(self._base(buffer_m=buffer_m))
        assert req.buffer_m == buffer_m

    @pytest.mark.parametrize("cost_mode", ["", "speed", "DISTANCE", "minutes"])
    def test_invalid_cost_mode(self, cost_mode: str) -> None:
        with pytest.raises(ValidationError):
            ClosestFacilityRequest.model_validate(self._base(cost_mode=cost_mode))

    def test_extra_field_rejected(self) -> None:
        d = self._base()
        d["something"] = "x"
        with pytest.raises(ValidationError):
            ClosestFacilityRequest.model_validate(d)


class TestRouteGeoJSON:
    def test_valid_linestring(self) -> None:
        g = RouteGeoJSON(coordinates=[[-117.16, 32.71], [-117.15, 32.72]])
        assert g.type == "LineString"
        assert len(g.coordinates) == 2

    def test_invalid_type(self) -> None:
        with pytest.raises(ValidationError):
            RouteGeoJSON.model_validate(
                {"type": "Point", "coordinates": [[0, 0]]}
            )


class TestFacilityResult:
    def test_minimal_row(self) -> None:
        r = FacilityResult(
            facility_id=1,
            rank=1,
            total_cost=100.0,
            total_distance_m=100.0,
            total_time_s=10.0,
        )
        assert r.route_geojson is None

    def test_with_geometry(self) -> None:
        r = FacilityResult(
            facility_id=1,
            rank=1,
            total_cost=100.0,
            total_distance_m=100.0,
            total_time_s=10.0,
            route_geojson=RouteGeoJSON(coordinates=[[0.0, 0.0], [1.0, 1.0]]),
        )
        assert r.route_geojson is not None
        assert len(r.route_geojson.coordinates) == 2


class TestClosestFacilityResponse:
    def test_empty_results(self) -> None:
        resp = ClosestFacilityResponse(request_id="abc", results=[])
        assert resp.cache_hit is False
        assert resp.results == []

    def test_roundtrip_json(self) -> None:
        resp = ClosestFacilityResponse(
            request_id="abc",
            results=[
                FacilityResult(
                    facility_id=1, rank=1, total_cost=1.0,
                    total_distance_m=1.0, total_time_s=1.0,
                )
            ],
            cache_hit=True,
        )
        payload = resp.model_dump_json()
        reparsed = ClosestFacilityResponse.model_validate_json(payload)
        assert reparsed == resp


class TestErrorResponse:
    def test_structure(self) -> None:
        e = ErrorResponse(
            request_id="abc", error_code="incident_off_graph", message="x"
        )
        d = e.model_dump()
        assert set(d.keys()) == {"request_id", "error_code", "message"}
