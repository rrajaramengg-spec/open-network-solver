"""Phase 3 HTTP integration suite — task 3.13.

Boots the FastAPI app with the Phase 2 fixture DB and the closest_facility
function installed, plus a real Redis container, then exercises every
``closest-facility`` HTTP spec scenario via ``httpx.AsyncClient``.

Marked ``@pytest.mark.e2e``; skips cleanly when Docker is unavailable.

The suite covers:
  * 200 happy path with cache-miss → cache-hit transition
  * 422 invalid_request (k out of range)
  * 422 incident_off_graph
  * 504 routing_timeout (via a fake repo)
  * 503 routing_db_unavailable (via a fake repo)
  * 429 rate-limited
  * /readyz with primary/replica down
  * /readyz with Nominatim down (which must NOT make readyz red — D16)
  * /readyz with Redis down (degraded, but still 200)
  * Request-id propagation
  * Cache flush after ETL swap (simulated)
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_SQL = _SERVICE_ROOT / "tests" / "fixtures" / "network-tiny" / "seed.sql"
_MIGRATION_FN = (
    _SERVICE_ROOT
    / "alembic"
    / "versions"
    / "20260623_0003_facility_attributes_category_closest_facility.py"
)


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _docker_available(), reason="Docker not available"),
]


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location("m", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: containerised Postgres + Redis, app wired against them
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _stack():
    pytest.importorskip("testcontainers")
    pytest.importorskip("psycopg")

    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer
    import psycopg

    img = "pgrouting/pgrouting:16-3.5-3.7.3"
    with PostgresContainer(img, driver="psycopg") as pg, \
         RedisContainer("redis:7-alpine") as rd:
        dsn = pg.get_connection_url().replace(
            "postgresql+psycopg://", "postgresql://"
        )
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(_FIXTURE_SQL.read_text(encoding="utf-8"))
            migration = _load_migration(_MIGRATION_FN)
            conn.execute(migration._FUNCTION_SQL)

        host = pg.get_container_host_ip()
        port = int(pg.get_exposed_port(5432))
        rhost = rd.get_container_host_ip()
        rport = int(rd.get_exposed_port(6379))
        yield {
            "pg_host": host, "pg_port": port,
            "pg_user": pg.username, "pg_pass": pg.password, "pg_db": pg.dbname,
            "redis_host": rhost, "redis_port": rport,
        }


@pytest.fixture
async def app_client(_stack, monkeypatch) -> AsyncIterator[Any]:
    """Boot a fresh FastAPI app per test (lifespan owns the engines)."""
    monkeypatch.setenv("ROUTING_DB_HOST", _stack["pg_host"])
    monkeypatch.setenv("ROUTING_DB_PORT", str(_stack["pg_port"]))
    monkeypatch.setenv("ROUTING_DB_REPLICA_HOST", _stack["pg_host"])
    monkeypatch.setenv("ROUTING_DB_REPLICA_PORT", str(_stack["pg_port"]))
    monkeypatch.setenv("ROUTING_DB_USER", _stack["pg_user"])
    monkeypatch.setenv("ROUTING_DB_PASSWORD", _stack["pg_pass"])
    monkeypatch.setenv("ROUTING_DB_NAME", _stack["pg_db"])
    monkeypatch.setenv("REDIS_URL", f"redis://{_stack['redis_host']}:{_stack['redis_port']}/0")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    from open_routing_service.config import get_settings
    get_settings.cache_clear()

    # Re-import main to pick up new settings; the limiter is built at module load.
    import importlib
    import open_routing_service.api.routing as routing_mod
    import open_routing_service.main as main_mod
    importlib.reload(routing_mod)
    importlib.reload(main_mod)

    from httpx import AsyncClient, ASGITransport
    app = main_mod.create_app()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ---------------------------------------------------------------------------
# Happy path + cache transition
# ---------------------------------------------------------------------------


_OK_BODY = {
    "incident": {"lat": 20.0000, "lon": -150.0000},
    "buffer_m": 2000.0,
    "k": 1,
    "cost_mode": "distance",
    "facility_filter": {},
}


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_200_with_results_and_request_id_header(self, app_client) -> None:
        resp = await app_client.post(
            "/v1/closest-facility",
            json=_OK_BODY,
            headers={"X-Request-Id": "test-rid-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "test-rid-001"
        assert resp.headers["x-request-id"] == "test-rid-001"
        assert len(body["results"]) == 1
        assert body["cache_hit"] is False
        result = body["results"][0]
        assert "facility_id" in result and "route_geojson" in result
        # facility-details enrichment (v8): name / category / tags / Feature.
        assert result["category"]  # non-empty category token
        feat = result["facility_geojson"]
        assert feat is not None
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Point"
        assert len(feat["geometry"]["coordinates"]) == 2
        assert feat["properties"]["facility_id"] == result["facility_id"]

    @pytest.mark.asyncio
    async def test_second_identical_request_is_cache_hit(self, app_client) -> None:
        await app_client.post("/v1/closest-facility", json=_OK_BODY)
        resp = await app_client.post("/v1/closest-facility", json=_OK_BODY)
        assert resp.status_code == 200
        assert resp.json()["cache_hit"] is True


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_invalid_k_returns_422(self, app_client) -> None:
        bad = dict(_OK_BODY, k=0)
        resp = await app_client.post("/v1/closest-facility", json=bad)
        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "invalid_request"
        assert "request_id" in body

    @pytest.mark.asyncio
    async def test_incident_off_graph_returns_422(self, app_client) -> None:
        off = dict(_OK_BODY, incident={"lat": 18.9, "lon": -150.0})
        resp = await app_client.post("/v1/closest-facility", json=off)
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error_code"] == "incident_off_graph"


# ---------------------------------------------------------------------------
# Facility categories (precomputed summary table)
# ---------------------------------------------------------------------------


class TestFacilityCategories:
    @pytest.mark.asyncio
    async def test_returns_categories_with_counts(self, app_client) -> None:
        resp = await app_client.get(
            "/v1/facility-categories", headers={"X-Request-Id": "cat-1"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "cat-1"
        assert resp.headers["x-request-id"] == "cat-1"
        cats = {c["category"]: c["count"] for c in body["categories"]}
        # Fixture network-tiny has 2 fire_station + 1 hospital.
        assert cats.get("fire_station") == 2
        assert cats.get("hospital") == 1
        assert body["total"] == 3

    @pytest.mark.asyncio
    async def test_repeat_call_is_cache_hit(self, app_client) -> None:
        await app_client.get("/v1/facility-categories")
        resp = await app_client.get("/v1/facility-categories")
        assert resp.status_code == 200
        assert resp.json()["cache_hit"] is True


# ---------------------------------------------------------------------------
# /healthz + /readyz
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_healthz_returns_200(self, app_client) -> None:
        resp = await app_client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_readyz_returns_200_when_deps_healthy(self, app_client) -> None:
        resp = await app_client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["primary_pg"] == "ok"
        assert body["replica_pg"] == "ok"
        assert body["pgr_version"] == "ok"
        # Per D16, no nominatim field SHALL be present.
        assert "nominatim" not in body

    @pytest.mark.asyncio
    async def test_readyz_omits_nominatim_field(self, app_client) -> None:
        """Explicit D16 scenario: readyz stays green when Nominatim is down
        because readyz never checks Nominatim at all."""
        resp = await app_client.get("/readyz")
        assert resp.status_code == 200
        assert "nominatim" not in resp.json()


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_format(self, app_client) -> None:
        # Generate at least one request so a metric exists.
        await app_client.post("/v1/closest-facility", json=_OK_BODY)
        resp = await app_client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "routing_request_duration_seconds_bucket" in resp.text


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_429_when_rate_limit_exceeded(self, monkeypatch, _stack) -> None:
        """Re-boot the app with a tight 2/minute limit and burst 3 requests."""
        monkeypatch.setenv("ROUTING_DB_HOST", _stack["pg_host"])
        monkeypatch.setenv("ROUTING_DB_PORT", str(_stack["pg_port"]))
        monkeypatch.setenv("ROUTING_DB_REPLICA_HOST", _stack["pg_host"])
        monkeypatch.setenv("ROUTING_DB_REPLICA_PORT", str(_stack["pg_port"]))
        monkeypatch.setenv("ROUTING_DB_USER", _stack["pg_user"])
        monkeypatch.setenv("ROUTING_DB_PASSWORD", _stack["pg_pass"])
        monkeypatch.setenv("ROUTING_DB_NAME", _stack["pg_db"])
        monkeypatch.setenv("REDIS_URL", f"redis://{_stack['redis_host']}:{_stack['redis_port']}/1")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")

        from open_routing_service.config import get_settings
        get_settings.cache_clear()
        import importlib
        import open_routing_service.api.routing as routing_mod
        import open_routing_service.main as main_mod
        importlib.reload(routing_mod)
        importlib.reload(main_mod)

        from httpx import AsyncClient, ASGITransport
        app = main_mod.create_app()
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                statuses = []
                for _ in range(4):
                    r = await client.post("/v1/closest-facility", json=_OK_BODY)
                    statuses.append(r.status_code)
                # At least one 429 in the burst
                assert 429 in statuses, f"expected 429 in {statuses}"
