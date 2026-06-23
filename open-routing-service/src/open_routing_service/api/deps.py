"""Dependency providers shared across API routes.

Lifespan owns the engines / clients / service; these accessors retrieve them
from ``request.app.state`` (set during lifespan startup).
"""

from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from open_routing_service.cache import ClosestFacilityCache
from open_routing_service.services import ClosestFacilityService


def get_primary_engine(request: Request) -> AsyncEngine:
    return request.app.state.primary_engine  # type: ignore[no-any-return]


def get_replica_engine(request: Request) -> AsyncEngine:
    return request.app.state.replica_engine  # type: ignore[no-any-return]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis  # type: ignore[no-any-return]


def get_cache(request: Request) -> ClosestFacilityCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def get_service(request: Request) -> ClosestFacilityService:
    return request.app.state.service  # type: ignore[no-any-return]
