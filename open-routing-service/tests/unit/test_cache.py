"""Unit tests for the Redis cache wrapper.

Covers: key construction, JSON round-trip, timeout behavior, best-effort
error swallowing (never raises to caller), namespace flush.

Implements task 3.12: "cache wrapper's key construction and timeout behavior".
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_routing_service.cache import ClosestFacilityCache


def _fake_redis(*, get_raises: BaseException | None = None,
                set_raises: BaseException | None = None,
                get_value: bytes | str | None = None,
                ping_value: bool = True) -> MagicMock:
    r = MagicMock()
    if get_raises is not None:
        r.get = AsyncMock(side_effect=get_raises)
    else:
        r.get = AsyncMock(return_value=get_value)
    if set_raises is not None:
        r.set = AsyncMock(side_effect=set_raises)
    else:
        r.set = AsyncMock(return_value=True)
    r.ping = AsyncMock(return_value=ping_value)
    r.delete = AsyncMock(return_value=1)

    async def _scan_iter(match: str, count: int):
        for k in ("cf:v1:a", "cf:v1:b", "cf:v1:c"):
            yield k

    r.scan_iter = _scan_iter
    return r


class TestKeyConstruction:
    def test_quantises_coords_to_4dp(self) -> None:
        cache = ClosestFacilityCache(_fake_redis(), function_version="v1")
        # Both inputs round to (32.7123, -117.1600) at 4dp.
        k1 = cache.build_key(
            lat=32.71231, lon=-117.16001, buffer_m=500.0,
            k=1, cost_mode="distance", facility_filter={},
        )
        k2 = cache.build_key(
            lat=32.71234, lon=-117.16004, buffer_m=500.0,
            k=1, cost_mode="distance", facility_filter={},
        )
        # Different at 5th decimal but same at 4dp → identical key
        assert k1 == k2

    def test_filter_order_independent(self) -> None:
        cache = ClosestFacilityCache(_fake_redis(), function_version="v1")
        k1 = cache.build_key(
            lat=0, lon=0, buffer_m=10, k=1, cost_mode="distance",
            facility_filter={"a": "1", "b": "2"},
        )
        k2 = cache.build_key(
            lat=0, lon=0, buffer_m=10, k=1, cost_mode="distance",
            facility_filter={"b": "2", "a": "1"},
        )
        assert k1 == k2

    def test_function_version_in_key(self) -> None:
        c1 = ClosestFacilityCache(_fake_redis(), function_version="v1")
        c2 = ClosestFacilityCache(_fake_redis(), function_version="v2")
        k1 = c1.build_key(lat=0, lon=0, buffer_m=10, k=1,
                          cost_mode="distance", facility_filter={})
        k2 = c2.build_key(lat=0, lon=0, buffer_m=10, k=1,
                          cost_mode="distance", facility_filter={})
        assert k1 != k2
        assert ":v1:" in k1
        assert ":v2:" in k2

    def test_key_starts_with_prefix(self) -> None:
        cache = ClosestFacilityCache(_fake_redis(), function_version="v1")
        k = cache.build_key(lat=0, lon=0, buffer_m=10, k=1,
                            cost_mode="distance", facility_filter={})
        assert k.startswith("cf:")


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self) -> None:
        cache = ClosestFacilityCache(
            _fake_redis(get_value=None), function_version="v1"
        )
        assert await cache.get("cf:v1:k") is None

    @pytest.mark.asyncio
    async def test_returns_parsed_json_on_hit(self) -> None:
        r = _fake_redis(get_value='{"results": [{"facility_id": 1}]}')
        cache = ClosestFacilityCache(r, function_version="v1")
        result = await cache.get("cf:v1:k")
        assert result == {"results": [{"facility_id": 1}]}

    @pytest.mark.asyncio
    async def test_swallows_redis_error(self) -> None:
        r = _fake_redis(get_raises=ConnectionError("boom"))
        cache = ClosestFacilityCache(r, function_version="v1")
        assert await cache.get("cf:v1:k") is None  # NEVER raises

    @pytest.mark.asyncio
    async def test_swallows_timeout(self) -> None:
        r = _fake_redis(get_raises=asyncio.TimeoutError())
        cache = ClosestFacilityCache(r, function_version="v1", timeout_s=0.05)
        assert await cache.get("cf:v1:k") is None

    @pytest.mark.asyncio
    async def test_swallows_invalid_json(self) -> None:
        r = _fake_redis(get_value="not-json")
        cache = ClosestFacilityCache(r, function_version="v1")
        assert await cache.get("cf:v1:k") is None


class TestSet:
    @pytest.mark.asyncio
    async def test_serialises_and_writes(self) -> None:
        r = _fake_redis()
        cache = ClosestFacilityCache(r, function_version="v1", ttl_s=600)
        ok = await cache.set("cf:v1:k", {"results": []})
        assert ok is True
        r.set.assert_awaited_once()
        args, kwargs = r.set.await_args
        assert args[0] == "cf:v1:k"
        assert '"results":[]' in args[1]
        assert kwargs["ex"] == 600

    @pytest.mark.asyncio
    async def test_returns_false_on_redis_error(self) -> None:
        r = _fake_redis(set_raises=ConnectionError("nope"))
        cache = ClosestFacilityCache(r, function_version="v1")
        assert await cache.set("cf:v1:k", {"x": 1}) is False  # NEVER raises

    @pytest.mark.asyncio
    async def test_returns_false_when_value_not_jsonable(self) -> None:
        cache = ClosestFacilityCache(_fake_redis(), function_version="v1")
        # set() default `str` handles datetime etc.; force a hard failure with
        # a recursion: object that's not JSON-encodable even via str.
        non_jsonable: dict[str, Any] = {}
        non_jsonable["self"] = non_jsonable  # circular reference
        assert await cache.set("cf:v1:k", non_jsonable) is False


class TestPing:
    @pytest.mark.asyncio
    async def test_true_when_redis_up(self) -> None:
        cache = ClosestFacilityCache(_fake_redis(), function_version="v1")
        assert await cache.ping() is True

    @pytest.mark.asyncio
    async def test_false_when_redis_down(self) -> None:
        r = MagicMock()
        r.ping = AsyncMock(side_effect=ConnectionError())
        cache = ClosestFacilityCache(r, function_version="v1")
        assert await cache.ping() is False


class TestFlushNamespace:
    @pytest.mark.asyncio
    async def test_deletes_all_matched_keys(self) -> None:
        r = _fake_redis()
        cache = ClosestFacilityCache(r, function_version="v1")
        deleted = await cache.flush_namespace()
        assert deleted == 3
        assert r.delete.await_count == 3
