"""Unit tests for ``ClosestFacilityService``.

Covers task 3.12: "the service's error-mapping logic" — cache → repo → cache
flow, exception propagation, cache-error swallowing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_routing_service.errors import (
    IncidentOffGraphError,
    RoutingTimeoutError,
)
from open_routing_service.services import ClosestFacilityService


def _fake_cache(*, get_returns: dict | None = None,
                set_returns: bool = True) -> MagicMock:
    c = MagicMock()
    c.build_key = MagicMock(return_value="cf:v1:key")
    c.categories_key = MagicMock(return_value="cfc:v1")
    c.get = AsyncMock(return_value=get_returns)
    c.set = AsyncMock(return_value=set_returns)
    return c


def _fake_repo(*, find_returns: list[dict] | None = None,
               find_raises: BaseException | None = None,
               list_returns: list[dict] | None = None,
               list_raises: BaseException | None = None) -> MagicMock:
    r = MagicMock()
    if find_raises is not None:
        r.find_closest = AsyncMock(side_effect=find_raises)
    else:
        r.find_closest = AsyncMock(return_value=find_returns or [])
    if list_raises is not None:
        r.list_categories = AsyncMock(side_effect=list_raises)
    else:
        r.list_categories = AsyncMock(return_value=list_returns or [])
    return r


_BASE_ARGS: dict[str, Any] = dict(
    lat=32.71, lon=-117.16, buffer_m=500.0,
    k=1, cost_mode="distance", facility_filter={},
)


class TestCacheHit:
    @pytest.mark.asyncio
    async def test_serves_from_cache_without_db_call(self) -> None:
        cache = _fake_cache(get_returns={"results": [{"facility_id": 7, "rank": 1}]})
        repo = _fake_repo()
        svc = ClosestFacilityService(repo=repo, cache=cache)

        results, cache_hit = await svc.find_closest(**_BASE_ARGS)

        assert cache_hit is True
        assert results == [{"facility_id": 7, "rank": 1}]
        repo.find_closest.assert_not_awaited()
        cache.set.assert_not_awaited()  # nothing to write back


class TestCacheMiss:
    @pytest.mark.asyncio
    async def test_calls_repo_and_writes_back(self) -> None:
        cache = _fake_cache(get_returns=None)
        repo_rows = [{"facility_id": 1, "rank": 1, "total_cost": 100.0}]
        repo = _fake_repo(find_returns=repo_rows)
        svc = ClosestFacilityService(repo=repo, cache=cache)

        results, cache_hit = await svc.find_closest(**_BASE_ARGS)

        assert cache_hit is False
        assert results == repo_rows
        repo.find_closest.assert_awaited_once_with(**_BASE_ARGS)
        cache.set.assert_awaited_once_with("cf:v1:key", {"results": repo_rows})


class TestErrorPropagation:
    @pytest.mark.asyncio
    async def test_off_graph_propagates_and_skips_cache_write(self) -> None:
        cache = _fake_cache(get_returns=None)
        repo = _fake_repo(find_raises=IncidentOffGraphError("off-graph"))
        svc = ClosestFacilityService(repo=repo, cache=cache)

        with pytest.raises(IncidentOffGraphError):
            await svc.find_closest(**_BASE_ARGS)

        cache.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_propagates_and_skips_cache_write(self) -> None:
        cache = _fake_cache(get_returns=None)
        repo = _fake_repo(find_raises=RoutingTimeoutError("slow"))
        svc = ClosestFacilityService(repo=repo, cache=cache)

        with pytest.raises(RoutingTimeoutError):
            await svc.find_closest(**_BASE_ARGS)

        cache.set.assert_not_awaited()


class TestCacheErrorTolerance:
    @pytest.mark.asyncio
    async def test_cache_get_returning_none_treated_as_miss(self) -> None:
        # Mirrors the wrapper's behaviour on Redis error (returns None).
        cache = _fake_cache(get_returns=None)
        repo = _fake_repo(find_returns=[{"facility_id": 1, "rank": 1}])
        svc = ClosestFacilityService(repo=repo, cache=cache)

        _, cache_hit = await svc.find_closest(**_BASE_ARGS)
        assert cache_hit is False
        repo.find_closest.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_set_failure_does_not_break_response(self) -> None:
        cache = _fake_cache(get_returns=None, set_returns=False)
        repo = _fake_repo(find_returns=[{"facility_id": 1, "rank": 1}])
        svc = ClosestFacilityService(repo=repo, cache=cache)

        results, cache_hit = await svc.find_closest(**_BASE_ARGS)
        assert cache_hit is False
        assert len(results) == 1  # still returned successfully


class TestListCategories:
    @pytest.mark.asyncio
    async def test_cache_miss_reads_repo_and_writes_back(self) -> None:
        cache = _fake_cache(get_returns=None)
        rows = [{"category": "fire_station", "count": 3},
                {"category": "hospital", "count": 1}]
        repo = _fake_repo(list_returns=rows)
        svc = ClosestFacilityService(repo=repo, cache=cache)

        categories, cache_hit = await svc.list_categories()

        assert cache_hit is False
        assert categories == rows
        repo.list_categories.assert_awaited_once_with()
        cache.set.assert_awaited_once_with("cfc:v1", {"categories": rows})

    @pytest.mark.asyncio
    async def test_cache_hit_skips_repo(self) -> None:
        rows = [{"category": "school", "count": 9}]
        cache = _fake_cache(get_returns={"categories": rows})
        repo = _fake_repo(list_returns=[])
        svc = ClosestFacilityService(repo=repo, cache=cache)

        categories, cache_hit = await svc.list_categories()

        assert cache_hit is True
        assert categories == rows
        repo.list_categories.assert_not_awaited()
        cache.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_catalog_returns_empty_list(self) -> None:
        cache = _fake_cache(get_returns=None)
        repo = _fake_repo(list_returns=[])
        svc = ClosestFacilityService(repo=repo, cache=cache)

        categories, cache_hit = await svc.list_categories()

        assert categories == []
        assert cache_hit is False
