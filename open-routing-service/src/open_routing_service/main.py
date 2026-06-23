"""FastAPI app factory for the routing service (design D5).

Lifespan owns:
  * ``primary_engine``  — async SQLAlchemy engine for healthchecks and future writes.
  * ``replica_engine``  — async SQLAlchemy engine for the ``closest_facility`` read.
  * ``redis``           — Redis client (shared connection pool).
  * ``cache``           — ``ClosestFacilityCache`` wrapper.
  * ``service``         — singleton ``ClosestFacilityService`` for DI.

No MCP / FastMCP imports — explicit non-goal per design D5.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from open_routing_service.api import (
    etl_status_router,
    health_router,
    metrics_router,
    routing_router,
)
from open_routing_service.api.routing import limiter
from open_routing_service.cache import ClosestFacilityCache
from open_routing_service.config import get_settings
from open_routing_service.models.api import ErrorResponse
from open_routing_service.observability import (
    RequestIdMiddleware,
    get_request_id,
    setup_logging,
)
from open_routing_service.repositories import RoutingRepository
from open_routing_service.services import ClosestFacilityService

LOG = logging.getLogger(__name__)


async def _read_function_version(engine: AsyncEngine, *, fallback: str) -> str:
    """Read the cache-key version from the ``function_version`` table (design D9).

    The service folds this into the Redis cache key so a function-body migration
    (which bumps the table) invalidates stale cached responses without an ETL
    rerun. Best-effort: any failure at startup falls back to the configured
    default rather than blocking boot.
    """
    try:
        async with engine.connect() as conn:
            version = (
                await conn.execute(
                    text("SELECT version FROM function_version WHERE id = 1")
                )
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — startup must never fail on a version read
        LOG.warning(
            "could not read function_version table; using fallback %r",
            fallback,
            exc_info=True,
        )
        return fallback
    if not version:
        LOG.warning("function_version table empty; using fallback %r", fallback)
        return fallback
    LOG.info("function_version resolved to %r from table", version)
    return str(version)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    LOG.info("startup: building engines and clients")

    primary_engine = create_async_engine(
        settings.routing_db_url_async,
        pool_size=settings.routing_db_pool_size,
        max_overflow=settings.routing_db_pool_max_overflow,
        pool_timeout=settings.routing_db_pool_timeout_s,
        pool_pre_ping=True,
    )
    replica_engine = create_async_engine(
        settings.routing_db_replica_url_async,
        pool_size=settings.routing_db_pool_size,
        max_overflow=settings.routing_db_pool_max_overflow,
        pool_timeout=settings.routing_db_pool_timeout_s,
        pool_pre_ping=True,
    )

    redis_client: Redis = Redis.from_url(
        str(settings.redis_url),
        socket_timeout=settings.redis_call_timeout_s,
        socket_connect_timeout=settings.redis_call_timeout_s,
        decode_responses=True,
    )

    # Fold the DB-side function_version into the cache key so a function-body
    # migration (which bumps the table) invalidates stale cached responses
    # without an ETL rerun (design D9). Read from the replica — the engine that
    # actually serves routing reads — so the cache version matches the data the
    # replica serves.
    function_version = await _read_function_version(
        replica_engine, fallback=settings.function_version
    )

    cache = ClosestFacilityCache(
        redis_client,
        function_version=function_version,
        ttl_s=settings.cache_ttl_s,
        timeout_s=settings.redis_call_timeout_s,
        key_prefix=settings.cache_key_prefix,
    )

    repo = RoutingRepository(replica_engine, timeout_s=settings.routing_call_timeout_s)
    service = ClosestFacilityService(repo=repo, cache=cache)

    app.state.primary_engine = primary_engine
    app.state.replica_engine = replica_engine
    app.state.redis = redis_client
    app.state.cache = cache
    app.state.repo = repo
    app.state.service = service
    app.state.shutdown_grace_s = settings.shutdown_grace_s

    LOG.info("startup complete; serving traffic")
    try:
        yield
    finally:
        # Graceful shutdown — uvicorn's --timeout-graceful-shutdown handles
        # request draining at the ASGI layer; here we close pools cleanly.
        LOG.info(
            "shutdown: closing engines and clients (grace=%.1fs)",
            settings.shutdown_grace_s,
        )
        await asyncio.sleep(0)  # let any in-flight handler observe the cancel
        await primary_engine.dispose()
        await replica_engine.dispose()
        await redis_client.aclose()
        LOG.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)

    app = FastAPI(
        title="open-routing-service",
        version="0.1.0",
        description="Closest-facility routing over OSM / pgRouting.",
        lifespan=lifespan,
    )

    # SlowAPI wiring (rate-limit handler + state attribute).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Outermost first: request-id BEFORE CORS so the id is set when CORS responds
    # to a preflight. ASGI middleware order is registration-order; FastAPI's
    # ``add_middleware`` reverses, so we register CORS first → it sits inside.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(RequestIdMiddleware)

    # Routers
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(routing_router)
    app.include_router(etl_status_router)

    # Centralised exception handlers — keep the error envelope stable.
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                request_id=get_request_id(),
                error_code="invalid_request",
                message=str(exc.errors()),
            ).model_dump(),
        )

    return app


app = create_app()
