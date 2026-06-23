"""Health endpoints per design D16.

``/healthz`` — liveness, always 200 while the process is up.
``/readyz``  — readiness, 200 ONLY when routing dependencies are healthy:
    * Primary DB pool acquires a connection within 1 s.
    * ``SELECT pgr_version()`` succeeds on the replica.
    * Redis ping is a soft check — failure → ``redis: degraded``, status stays
      ``ok`` (cache is best-effort). The ``cache_error_total`` counter ticks.
``/readyz`` deliberately does NOT check Nominatim (D16, fixed-in-explore bug).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from open_routing_service.api.deps import (
    get_cache,
    get_primary_engine,
    get_replica_engine,
)
from open_routing_service.cache import ClosestFacilityCache
from open_routing_service.models.api import HealthResponse, ReadyzResponse
from open_routing_service.observability import cache_error_total

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness probe — process is up."""
    return HealthResponse(status="ok")


async def _check_primary(engine: AsyncEngine, timeout_s: float = 1.0) -> bool:
    try:
        async with asyncio.timeout(timeout_s):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("primary readyz failed: %s", exc)
        return False


async def _check_replica(engine: AsyncEngine, timeout_s: float = 1.0) -> tuple[bool, bool]:
    """Returns ``(connection_ok, pgr_version_ok)``."""
    try:
        async with asyncio.timeout(timeout_s):
            async with engine.connect() as conn:
                row = await conn.execute(text("SELECT pgr_version()"))
                _ = row.scalar_one_or_none()
        return True, True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("replica readyz failed: %s", exc)
        return False, False


@router.get(
    "/readyz",
    response_model=ReadyzResponse,
    responses={503: {"model": ReadyzResponse}},
)
async def readyz(
    response: Response,
    primary: AsyncEngine = Depends(get_primary_engine),
    replica: AsyncEngine = Depends(get_replica_engine),
    cache: ClosestFacilityCache = Depends(get_cache),
) -> ReadyzResponse:
    primary_ok = await _check_primary(primary)
    replica_ok, pgr_ok = await _check_replica(replica)
    redis_ok = await cache.ping()
    if not redis_ok:
        cache_error_total.inc()

    overall_ok = primary_ok and replica_ok and pgr_ok
    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyzResponse(
        status="ok" if overall_ok else "degraded",
        primary_pg="ok" if primary_ok else "error",
        replica_pg="ok" if replica_ok else "error",
        pgr_version="ok" if pgr_ok else "error",
        redis="ok" if redis_ok else "degraded",
    )
