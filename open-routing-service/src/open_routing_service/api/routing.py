"""``/v1/closest-facility`` HTTP endpoint.

Maps domain exceptions to error responses per the spec:
  * ``IncidentOffGraphError``     → 422 ``incident_off_graph``
  * ``RoutingTimeoutError``       → 504 ``routing_timeout``
  * ``RoutingDBUnavailableError`` → 503 ``routing_db_unavailable``

Per-request metrics (``routing_request_*``, ``routing_cache_*``,
``closest_facility_results_count``) are recorded around every call so the
dashboard and SLO panels work without app-wide middleware.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from open_routing_service.api.deps import get_service
from open_routing_service.config import get_settings
from open_routing_service.errors import (
    IncidentOffGraphError,
    RoutingDBUnavailableError,
    RoutingTimeoutError,
)
from open_routing_service.models.api import (
    ClosestFacilityRequest,
    ClosestFacilityResponse,
    ErrorResponse,
    FacilityResult,
)
from open_routing_service.observability import (
    cache_hit_total,
    cache_miss_total,
    closest_facility_results_count,
    get_request_id,
    request_duration_seconds,
    request_total,
)
from open_routing_service.services import ClosestFacilityService

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["routing"])

# SlowAPI limiter is built per-request from the app's shared instance — see
# ``main.py`` lifespan. The decorator below uses a closure over the function
# so the rate is read from settings at module import time.
_settings = get_settings()
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/closest-facility",
    response_model=ClosestFacilityResponse,
    responses={
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
@limiter.limit(f"{_settings.rate_limit_per_minute}/minute")
async def closest_facility(
    request: Request,  # required by SlowAPI for IP-based limiting
    body: ClosestFacilityRequest,
    service: ClosestFacilityService = Depends(get_service),
) -> ClosestFacilityResponse:
    endpoint = "/v1/closest-facility"
    started = time.perf_counter()
    rid = get_request_id()

    try:
        results, cache_hit = await service.find_closest(
            lat=body.incident.lat,
            lon=body.incident.lon,
            buffer_m=body.buffer_m,
            k=body.k,
            cost_mode=body.cost_mode,
            facility_filter=body.facility_filter,
        )
    except IncidentOffGraphError as exc:
        _record(endpoint, "error", status.HTTP_422_UNPROCESSABLE_ENTITY, started)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                request_id=rid,
                error_code=exc.error_code,
                message=exc.message,
            ).model_dump(),
        ) from exc
    except RoutingTimeoutError as exc:
        _record(endpoint, "error", status.HTTP_504_GATEWAY_TIMEOUT, started)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=ErrorResponse(
                request_id=rid,
                error_code=exc.error_code,
                message=exc.message,
            ).model_dump(),
        ) from exc
    except RoutingDBUnavailableError as exc:
        _record(endpoint, "error", status.HTTP_503_SERVICE_UNAVAILABLE, started)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorResponse(
                request_id=rid,
                error_code=exc.error_code,
                message=exc.message,
            ).model_dump(),
        ) from exc

    if cache_hit:
        cache_hit_total.inc()
    else:
        cache_miss_total.inc()
    closest_facility_results_count.observe(len(results))
    _record(endpoint, "ok", status.HTTP_200_OK, started)

    return ClosestFacilityResponse(
        request_id=rid,
        results=[FacilityResult(**r) for r in results],
        cache_hit=cache_hit,
    )


def _record(endpoint: str, result: str, code: int, started: float) -> None:
    request_total.labels(endpoint=endpoint, status_code=str(code)).inc()
    request_duration_seconds.labels(endpoint=endpoint, result=result).observe(
        time.perf_counter() - started
    )
