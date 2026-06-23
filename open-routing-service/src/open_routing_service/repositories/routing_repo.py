"""Async SQLAlchemy repository for the ``closest_facility`` PL/pgSQL function.

Reads go to the **replica** engine (design D14). The repository is a thin
wrapper over a SELECT against the function — all routing logic lives in the
PL/pgSQL function itself.

Per ``platform-engineering`` skill §asyncio: a per-call ``asyncio.timeout``
guards against runaway queries. On timeout the repo raises
``RoutingTimeoutError`` which the service layer maps to HTTP 504.

The function's ``incident_off_graph`` exception (SQLSTATE P0001) is caught
here and re-raised as ``IncidentOffGraphError`` for the service layer to map
to HTTP 422.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from open_routing_service.errors import (
    IncidentOffGraphError,
    RoutingDBUnavailableError,
    RoutingTimeoutError,
)

LOG = logging.getLogger(__name__)


# SQL — explicit cast on incident keeps the function signature unambiguous.
_CALL_SQL = text(
    """
    SELECT
        facility_id,
        rank,
        total_cost,
        total_distance_m,
        total_time_s,
        ST_AsGeoJSON(route_geom) AS route_geojson,
        facility_name,
        facility_category,
        facility_tags::text       AS facility_tags,
        ST_AsGeoJSON(facility_geom) AS facility_point_geojson
    FROM closest_facility(
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geometry(Point, 4326),
        :buffer_m,
        CAST(:facility_filter AS jsonb),
        :k,
        :cost_mode
    )
    ORDER BY rank
    """
)


class RoutingRepository:
    """Calls ``SELECT * FROM closest_facility(...)`` on the replica."""

    def __init__(self, replica_engine: AsyncEngine, *, timeout_s: float = 5.0) -> None:
        self._engine = replica_engine
        self._timeout_s = timeout_s

    async def find_closest(
        self,
        *,
        lat: float,
        lon: float,
        buffer_m: float,
        k: int,
        cost_mode: str,
        facility_filter: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute closest_facility and return rows as dicts.

        Raises:
            IncidentOffGraphError:  function raised ``incident_off_graph``.
            RoutingTimeoutError:    DB call exceeded ``timeout_s``.
            RoutingDBUnavailableError: connection pool / replica unreachable.
        """
        params = {
            "lat": lat,
            "lon": lon,
            "buffer_m": buffer_m,
            "k": k,
            "cost_mode": cost_mode,
            "facility_filter": json.dumps(facility_filter or {}),
        }
        try:
            async with asyncio.timeout(self._timeout_s):
                async with self._engine.connect() as conn:
                    result = await conn.execute(_CALL_SQL, params)
                    rows = result.mappings().all()
        except (TimeoutError, asyncio.TimeoutError) as exc:
            LOG.warning(
                "closest_facility timeout (>%ss)",
                self._timeout_s,
                extra={"buffer_m": buffer_m, "k": k},
            )
            raise RoutingTimeoutError(
                f"routing query exceeded {self._timeout_s} s"
            ) from exc
        except OperationalError as exc:
            LOG.error("replica unavailable: %s", exc)
            raise RoutingDBUnavailableError("routing replica unavailable") from exc
        except DBAPIError as exc:
            msg = str(exc).lower()
            if "incident_off_graph" in msg:
                raise IncidentOffGraphError(
                    "incident point is too far from any road (> 250 m)"
                ) from exc
            LOG.exception("unexpected DBAPI error in closest_facility")
            raise

        # Parse GeoJSON in Python — keeps SQL portable.
        return [_row_to_result(dict(row)) for row in rows]


def _row_to_result(d: dict[str, Any]) -> dict[str, Any]:
    """Convert one raw ``closest_facility`` row into a ``FacilityResult`` dict.

    Pure (no I/O) so it is unit-testable without a database: it parses the
    ``ST_AsGeoJSON`` text columns and assembles the facility ``Feature``.
    """
    # Route geometry (LineString) → GeoJSON object or None.
    raw_route = d.pop("route_geojson", None)
    if raw_route:
        try:
            d["route_geojson"] = json.loads(raw_route)
        except (ValueError, TypeError):
            d["route_geojson"] = None
    else:
        d["route_geojson"] = None

    # Facility attributes → assemble an RFC 7946 Feature (Point) so the UI
    # renders the real marker + popup without the route-endpoint hack.
    name = d.pop("facility_name", None)
    category = d.pop("facility_category", None) or "other"
    raw_tags = d.pop("facility_tags", None)
    raw_point = d.pop("facility_point_geojson", None)
    try:
        tags = json.loads(raw_tags) if raw_tags else {}
    except (ValueError, TypeError):
        tags = {}
    try:
        geometry = json.loads(raw_point) if raw_point else None
    except (ValueError, TypeError):
        geometry = None

    d["name"] = name
    d["category"] = category
    d["tags"] = tags
    d["facility_geojson"] = (
        {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "facility_id": d["facility_id"],
                "name": name,
                "category": category,
                "tags": tags,
            },
        }
        if geometry is not None
        else None
    )
    return d
