"""Phase 5 unit tests — lifespan teardown disposes engines + Redis cleanly.

Implements task 5.10 (graceful-shutdown handler coverage).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_routing_service.main import _read_function_version, create_app


class TestLifespanTeardown:
    @pytest.mark.asyncio
    async def test_lifespan_disposes_engines_and_redis(self) -> None:
        primary = MagicMock(name="primary_engine")
        primary.dispose = AsyncMock()
        replica = MagicMock(name="replica_engine")
        replica.dispose = AsyncMock()
        redis_client = MagicMock(name="redis_client")
        redis_client.aclose = AsyncMock()

        with patch("open_routing_service.main.create_async_engine") as mk_engine, \
             patch("open_routing_service.main.Redis") as mk_redis, \
             patch(
                 "open_routing_service.main._read_function_version",
                 new=AsyncMock(return_value="v1"),
             ):
            mk_engine.side_effect = [primary, replica]
            mk_redis.from_url.return_value = redis_client

            app = create_app()

            async with app.router.lifespan_context(app):
                # Sanity — state is wired
                assert app.state.primary_engine is primary
                assert app.state.replica_engine is replica
                assert app.state.redis is redis_client

            primary.dispose.assert_awaited_once()
            replica.dispose.assert_awaited_once()
            redis_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_grace_setting_is_exposed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHUTDOWN_GRACE_S", "12.5")
        from open_routing_service.config import get_settings
        get_settings.cache_clear()
        try:
            primary = MagicMock(name="primary_engine")
            primary.dispose = AsyncMock()
            replica = MagicMock(name="replica_engine")
            replica.dispose = AsyncMock()
            redis_client = MagicMock()
            redis_client.aclose = AsyncMock()
            with patch("open_routing_service.main.create_async_engine") as mk_engine, \
                 patch("open_routing_service.main.Redis") as mk_redis, \
                 patch(
                     "open_routing_service.main._read_function_version",
                     new=AsyncMock(return_value="v1"),
                 ):
                mk_engine.side_effect = [primary, replica]
                mk_redis.from_url.return_value = redis_client
                app = create_app()
                async with app.router.lifespan_context(app):
                    assert app.state.shutdown_grace_s == 12.5
        finally:
            get_settings.cache_clear()


def _fake_engine(*, scalar: object = None, raises: BaseException | None = None) -> MagicMock:
    """Build a MagicMock AsyncEngine whose ``connect()`` is an async CM."""

    class _Result:
        def scalar_one_or_none(self) -> object:
            return scalar

    conn = AsyncMock()
    if raises is not None:
        conn.execute = AsyncMock(side_effect=raises)
    else:
        conn.execute = AsyncMock(return_value=_Result())
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect = MagicMock(return_value=cm)
    return engine


class TestReadFunctionVersion:
    @pytest.mark.asyncio
    async def test_returns_table_version(self) -> None:
        version = await _read_function_version(_fake_engine(scalar="v6"), fallback="v1")
        assert version == "v6"

    @pytest.mark.asyncio
    async def test_empty_table_falls_back(self) -> None:
        version = await _read_function_version(_fake_engine(scalar=None), fallback="v1")
        assert version == "v1"

    @pytest.mark.asyncio
    async def test_db_error_falls_back(self) -> None:
        version = await _read_function_version(
            _fake_engine(raises=RuntimeError("boom")), fallback="v1"
        )
        assert version == "v1"
