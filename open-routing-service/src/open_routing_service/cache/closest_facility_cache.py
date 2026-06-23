"""Redis cache wrapper for ``closest_facility`` responses.

Key schema (design D9):

    cf:<function_version>:<lat_4dp>:<lon_4dp>:<buffer_m>:<k>:<cost_mode>:<filter_sha1>

  * ``cf:`` namespace prefix — lets the ETL flush by ``SCAN MATCH cf:*``.
  * ``function_version`` — bumped by any migration that changes the PL/pgSQL
    function body, so a function-only change invalidates cached responses.
  * Coordinates quantised to 4 decimal places (~11 m at the equator) so
    near-identical incidents share a cache entry.
  * ``filter_sha1`` — short fingerprint of the JSON-serialised filter to
    keep keys bounded.

The wrapper is **best-effort**: a Redis outage logs and increments a counter
but never breaks the request (per ``scalability-observability`` §caching).
Per-call timeout is 500 ms (``settings.redis_call_timeout_s``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from redis.asyncio import Redis

LOG = logging.getLogger(__name__)


class CacheError(Exception):
    """Raised internally; never propagated to callers — caught by the wrapper."""


class ClosestFacilityCache:
    """Best-effort Redis cache for closest-facility responses.

    Construct once at app lifespan startup; the ``Redis`` client owns its own
    connection pool. ``aclose()`` should be called at lifespan shutdown.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        function_version: str,
        ttl_s: int = 3600,
        timeout_s: float = 0.5,
        key_prefix: str = "cf:",
    ) -> None:
        self._redis = redis
        self._function_version = function_version
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._key_prefix = key_prefix

    # --- key building ----------------------------------------------------- #

    def build_key(
        self,
        *,
        lat: float,
        lon: float,
        buffer_m: float,
        k: int,
        cost_mode: str,
        facility_filter: dict[str, Any],
    ) -> str:
        """Construct the cache key for a request.

        The filter is serialised with ``sort_keys=True`` so semantically
        identical dicts produce the same key regardless of insertion order.
        """
        filter_json = json.dumps(facility_filter, sort_keys=True, separators=(",", ":"))
        filter_sha = hashlib.sha1(filter_json.encode("utf-8")).hexdigest()[:12]
        return (
            f"{self._key_prefix}{self._function_version}:"
            f"{lat:.4f}:{lon:.4f}:{buffer_m:g}:{k}:{cost_mode}:{filter_sha}"
        )

    def categories_key(self) -> str:
        """Cache key for the facility-categories summary (``cfc:`` namespace).

        Keyed on ``function_version`` and flushed at the ETL swap alongside the
        ``cf:*`` namespace (best-effort, design D4).
        """
        return f"cfc:{self._function_version}"

    # --- get / set -------------------------------------------------------- #

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached payload or ``None`` on miss or error.

        Never raises — a Redis error is logged + counted and treated as a miss.
        """
        try:
            async with asyncio.timeout(self._timeout_s):
                raw = await self._redis.get(key)
        except (TimeoutError, asyncio.TimeoutError):
            LOG.warning("cache get timeout", extra={"key": key})
            return None
        except Exception as exc:  # noqa: BLE001 — best-effort
            LOG.warning("cache get error: %s", exc, extra={"key": key})
            return None

        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            LOG.warning("cache get json-decode error: %s", exc, extra={"key": key})
            return None

    async def set(self, key: str, value: dict[str, Any]) -> bool:
        """Persist ``value`` under ``key`` with TTL.

        Returns True on success, False on any failure. Never raises.
        """
        try:
            payload = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError) as exc:
            LOG.warning("cache set json-encode error: %s", exc, extra={"key": key})
            return False

        try:
            async with asyncio.timeout(self._timeout_s):
                await self._redis.set(key, payload, ex=self._ttl_s)
            return True
        except (TimeoutError, asyncio.TimeoutError):
            LOG.warning("cache set timeout", extra={"key": key})
            return False
        except Exception as exc:  # noqa: BLE001
            LOG.warning("cache set error: %s", exc, extra={"key": key})
            return False

    # --- flush ----------------------------------------------------------- #

    async def flush_namespace(self) -> int:
        """Delete every key matching ``<prefix>*`` (the cf:* namespace).

        Invoked by the post-swap webhook (design D9). Uses SCAN to avoid the
        blocking ``KEYS *`` anti-pattern.
        """
        deleted = 0
        pattern = f"{self._key_prefix}*"
        try:
            async with asyncio.timeout(self._timeout_s * 10):  # bulk op
                async for key in self._redis.scan_iter(match=pattern, count=500):
                    await self._redis.delete(key)
                    deleted += 1
        except Exception as exc:  # noqa: BLE001
            LOG.error("cache flush error: %s", exc)
            return deleted
        LOG.info("cache namespace flushed", extra={"pattern": pattern, "deleted": deleted})
        return deleted

    # --- ping ------------------------------------------------------------ #

    async def ping(self) -> bool:
        """Return True if Redis is reachable within the timeout."""
        try:
            async with asyncio.timeout(self._timeout_s):
                return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False
