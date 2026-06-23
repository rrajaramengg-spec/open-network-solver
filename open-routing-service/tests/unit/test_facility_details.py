"""Unit tests for the facility-details enrichment (no DB required).

Covers Phase 1 task 1.7:
  * ``FacilityGeometry`` / ``FacilityFeature`` / ``FacilityResult`` model
    validation + serialization round-trip.
  * ``routing_repo._row_to_result`` GeoJSON-Feature assembly from a raw row.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from open_routing_service.models.api import (
    FacilityFeature,
    FacilityGeometry,
    FacilityResult,
)
from open_routing_service.repositories.routing_repo import _row_to_result


class TestFacilityModels:
    def test_facility_geometry_round_trip(self) -> None:
        g = FacilityGeometry(coordinates=[-117.16, 32.71])
        assert g.type == "Point"
        assert FacilityGeometry.model_validate(g.model_dump()) == g

    def test_facility_feature_round_trip(self) -> None:
        feat = FacilityFeature(
            geometry=FacilityGeometry(coordinates=[-117.16, 32.71]),
            properties={"facility_id": 1, "name": "Station 7", "category": "fire_station", "tags": {}},
        )
        assert feat.type == "Feature"
        assert FacilityFeature.model_validate(feat.model_dump()) == feat

    def test_facility_feature_rejects_extra(self) -> None:
        with pytest.raises(ValidationError):
            FacilityFeature(
                geometry=FacilityGeometry(coordinates=[0.0, 0.0]),
                properties={},
                bbox=[0, 0, 0, 0],  # type: ignore[call-arg]
            )

    def test_facility_result_defaults_are_additive(self) -> None:
        r = FacilityResult(
            facility_id=1, rank=1, total_cost=1.0,
            total_distance_m=2.0, total_time_s=3.0,
        )
        assert r.name is None
        assert r.category == "other"
        assert r.tags == {}
        assert r.facility_geojson is None

    def test_facility_result_full_payload(self) -> None:
        payload = {
            "facility_id": 9,
            "rank": 1,
            "total_cost": 110.0,
            "total_distance_m": 110.0,
            "total_time_s": 4.0,
            "route_geojson": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            "name": "Fire Station C",
            "category": "fire_station",
            "tags": {"amenity": "fire_station"},
            "facility_geojson": {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
                "properties": {"facility_id": 9, "name": "Fire Station C",
                               "category": "fire_station", "tags": {"amenity": "fire_station"}},
            },
        }
        r = FacilityResult.model_validate(payload)
        assert r.category == "fire_station"
        assert r.facility_geojson is not None
        assert r.facility_geojson.geometry.coordinates == [1.0, 1.0]


class TestRowToResult:
    def _raw_row(self, **over: object) -> dict[str, object]:
        d: dict[str, object] = {
            "facility_id": 9,
            "rank": 1,
            "total_cost": 110.0,
            "total_distance_m": 110.0,
            "total_time_s": 4.0,
            "route_geojson": json.dumps({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}),
            "facility_name": "Fire Station C",
            "facility_category": "fire_station",
            "facility_tags": json.dumps({"amenity": "fire_station"}),
            "facility_point_geojson": json.dumps({"type": "Point", "coordinates": [1.0, 1.0]}),
        }
        d.update(over)
        return d

    def test_assembles_facility_feature(self) -> None:
        out = _row_to_result(self._raw_row())
        assert out["name"] == "Fire Station C"
        assert out["category"] == "fire_station"
        assert out["tags"] == {"amenity": "fire_station"}
        feat = out["facility_geojson"]
        assert feat["type"] == "Feature"
        assert feat["geometry"] == {"type": "Point", "coordinates": [1.0, 1.0]}
        assert feat["properties"]["facility_id"] == 9
        assert feat["properties"]["category"] == "fire_station"

    def test_result_is_valid_facility_result(self) -> None:
        out = _row_to_result(self._raw_row())
        # The assembled dict must validate against the response model.
        FacilityResult.model_validate(out)

    def test_missing_facility_geom_yields_none(self) -> None:
        out = _row_to_result(self._raw_row(facility_point_geojson=None))
        assert out["facility_geojson"] is None

    def test_null_category_falls_back_to_other(self) -> None:
        out = _row_to_result(self._raw_row(facility_category=None))
        assert out["category"] == "other"

    def test_malformed_tags_default_to_empty(self) -> None:
        out = _row_to_result(self._raw_row(facility_tags="not-json"))
        assert out["tags"] == {}

    def test_missing_route_geojson_is_none(self) -> None:
        out = _row_to_result(self._raw_row(route_geojson=None))
        assert out["route_geojson"] is None
