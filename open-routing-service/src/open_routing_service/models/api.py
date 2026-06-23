"""Pydantic v2 request / response models for ``/v1/closest-facility``.

Per task 3.3 + ``closest-facility`` spec — coordinate validation, k ∈ [1, 10],
buffer_m ∈ [10, 16 093.44] (10-mile cap; scalable-routing-etl design D8),
cost_mode ∈ {distance, time}, facility_filter as a
free-form ``dict[str, Any]``.

Geometry payloads are kept simple — incident is a lat/lon pair (not GeoJSON)
because the UI ships exactly that. Route geometry returned to the client is
GeoJSON (LineString) for direct MapLibre consumption.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class IncidentPoint(BaseModel):
    """WGS-84 lat/lon pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lat: Annotated[float, Field(ge=-90.0, le=90.0)]
    lon: Annotated[float, Field(ge=-180.0, le=180.0)]


class ClosestFacilityRequest(BaseModel):
    """Body for ``POST /v1/closest-facility``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    incident: IncidentPoint
    buffer_m: Annotated[
        float,
        Field(
            ge=10.0,
            le=16_093.44,
            description=(
                "Search radius in metres around the incident. "
                "Capped at 10 miles (16 093.44 m) so the bbox-bounded routing "
                "query stays size-independent (scalable-routing-etl design D8)."
            ),
        ),
    ] = 152.4  # 500 ft — UI default
    k: Annotated[int, Field(ge=1, le=10)] = 1
    cost_mode: Literal["distance", "time"] = "distance"
    facility_filter: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RouteGeoJSON(BaseModel):
    """A GeoJSON LineString — the route geometry from incident to facility."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]


class FacilityGeometry(BaseModel):
    """A GeoJSON Point geometry (RFC 7946) for the facility location."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"] = "Point"
    coordinates: list[float]


class FacilityFeature(BaseModel):
    """A GeoJSON ``Feature`` wrapping the facility point + its properties.

    ``properties`` carries ``facility_id``, ``name``, ``category`` and the raw
    OSM ``tags`` so the UI can render a category-specific marker and a
    click-through popup directly from the feature (no extra lookup).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"] = "Feature"
    geometry: FacilityGeometry
    properties: dict[str, Any]


class FacilityResult(BaseModel):
    """One row of the ``closest_facility()`` result, ranked by total_cost."""

    model_config = ConfigDict(extra="forbid")

    facility_id: int
    rank: int
    total_cost: float
    total_distance_m: float
    total_time_s: float
    route_geojson: RouteGeoJSON | None = None
    # Facility identity + geometry (additive — facility-details change).
    name: str | None = None
    category: str = "other"
    tags: dict[str, Any] = Field(default_factory=dict)
    facility_geojson: FacilityFeature | None = None


class ClosestFacilityResponse(BaseModel):
    """Body for ``200 POST /v1/closest-facility``."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    results: list[FacilityResult]
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Stable error envelope for non-2xx responses."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    error_code: str
    message: str


# ---------------------------------------------------------------------------
# Health / readyz responses
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class ReadyzResponse(BaseModel):
    """Per design D16: routing-only readiness; Nominatim is NOT checked."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    primary_pg: Literal["ok", "error"]
    replica_pg: Literal["ok", "error"]
    pgr_version: Literal["ok", "error"]
    redis: Literal["ok", "degraded"]
