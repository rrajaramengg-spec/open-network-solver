"""Orchestrates the cache → repo → cache flow for closest-facility requests.

Per task 3.5 + design D5:
  1. Build the cache key.
  2. Cache GET (best-effort; treat error as miss).
  3. Repo call (with timeout).
  4. Cache SET (best-effort).
  5. Map domain exceptions to error codes that the API layer converts to HTTP.

The service depends on **protocols** rather than concrete classes — both the
repo and the cache are passed in by the FastAPI ``Depends``-wired lifespan
provider. Tests can substitute fakes without touching the network.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from open_routing_service.errors import IncidentOffGraphError, RoutingTimeoutError

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols — what the service expects from its collaborators
# ---------------------------------------------------------------------------


class _Repo(Protocol):
    async def find_closest(
        self,
        *,
        lat: float,
        lon: float,
        buffer_m: float,
        k: int,
        cost_mode: str,
        facility_filter: dict[str, Any],
    ) -> list[dict[str, Any]]: ...


class _Cache(Protocol):
    def build_key(
        self,
        *,
        lat: float,
        lon: float,
        buffer_m: float,
        k: int,
        cost_mode: str,
        facility_filter: dict[str, Any],
    ) -> str: ...
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def set(self, key: str, value: dict[str, Any]) -> bool: ...


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ClosestFacilityService:
    """Stateless orchestrator — safe to share across requests via DI."""

    def __init__(self, repo: _Repo, cache: _Cache) -> None:
        self._repo = repo
        self._cache = cache

    async def find_closest(
        self,
        *,
        lat: float,
        lon: float,
        buffer_m: float,
        k: int,
        cost_mode: str,
        facility_filter: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return ``(results, cache_hit)``.

        Raises:
            IncidentOffGraphError, RoutingTimeoutError,
            RoutingDBUnavailableError — propagated from the repo for the
            API layer to map to HTTP status codes.
        """
        key = self._cache.build_key(
            lat=lat,
            lon=lon,
            buffer_m=buffer_m,
            k=k,
            cost_mode=cost_mode,
            facility_filter=facility_filter,
        )

        cached = await self._cache.get(key)
        if cached is not None:
            LOG.debug("cache hit", extra={"key": key})
            return cached.get("results", []), True

        try:
            results = await self._repo.find_closest(
                lat=lat,
                lon=lon,
                buffer_m=buffer_m,
                k=k,
                cost_mode=cost_mode,
                facility_filter=facility_filter,
            )
        except IncidentOffGraphError:
            # Don't cache — caller might move the incident; let the next call
            # recompute. Re-raise for API mapping.
            raise
        except RoutingTimeoutError:
            # Don't cache. Re-raise for API mapping.
            raise

        # Best-effort write; failure logged inside the cache wrapper.
        await self._cache.set(key, {"results": results})

        return results, False
