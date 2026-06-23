"""Integration-test fixtures: per-module shared Postgres + Redis containers.

These fixtures are shared across all files in tests/integration/ through
conftest.py pytest fixture scoping. Each module that needs a live Postgres
gets the ``live_db`` fixture; each that needs Redis gets ``live_redis``.

Design notes:
  * We use the same upstream pgRouting image as the production compose stack
    so any PG-version / extension-version mismatch surfaces here rather than
    at staging time.
  * testcontainers boots/tears down its own containers; these are separate
    from the infra/docker-compose.yml dev stack.
  * The ``apply_alembic`` fixture runs ``alembic upgrade head`` using the
    service's real migration code — this proves the migration is sound.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Generator

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _SERVICE_ROOT / "alembic.ini"
_ETL_DIR = _SERVICE_ROOT.parent / "infra" / "etl"

# Add ETL dir to path so tests can import load_osm.
if str(_ETL_DIR) not in sys.path:
    sys.path.insert(0, str(_ETL_DIR))


# --------------------------------------------------------------------------- #
# Docker availability guard
# --------------------------------------------------------------------------- #


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


DOCKER = _docker_available()
pytestmark = pytest.mark.skipif(not DOCKER, reason="Docker not available")


# --------------------------------------------------------------------------- #
# Shared Postgres fixture (module scope = one container per test module)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def live_db(monkeypatch_module: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Provide a live Postgres for integration tests.

    Priority:
      1. If ROUTING_DB_HOST / ROUTING_DB_PORT / ROUTING_DB_PASSWORD are already
         set (e.g. the dev compose stack is running), use them directly.
      2. Otherwise, boot a fresh testcontainers Postgres.

    This lets CI and dev machines both run integration tests without changes.
    """
    already_configured = all(
        os.environ.get(k) for k in
        ("ROUTING_DB_HOST", "ROUTING_DB_PORT", "ROUTING_DB_USER", "ROUTING_DB_PASSWORD", "ROUTING_DB_NAME")
    )

    if already_configured:
        # Ensure extensions exist (idempotent).
        import psycopg2
        with psycopg2.connect(
            host=os.environ["ROUTING_DB_HOST"],
            port=int(os.environ["ROUTING_DB_PORT"]),
            user=os.environ["ROUTING_DB_USER"],
            password=os.environ["ROUTING_DB_PASSWORD"],
            dbname=os.environ["ROUTING_DB_NAME"],
        ) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis_topology;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgrouting;")
        host = os.environ["ROUTING_DB_HOST"]
        port = os.environ["ROUTING_DB_PORT"]
        user = os.environ["ROUTING_DB_USER"]
        pw = os.environ["ROUTING_DB_PASSWORD"]
        db = os.environ["ROUTING_DB_NAME"]
        yield f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{db}"
        return

    if not DOCKER:
        pytest.skip("Docker not available and no pre-configured DB found")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "pgrouting/pgrouting:16-3.5-3.7.3",
        username="routing",
        password="routing_test",
        dbname="routing",
    ) as pg:
        _run_init_sql(pg)
        monkeypatch_module.setenv("ROUTING_DB_HOST", pg.get_container_host_ip())
        monkeypatch_module.setenv("ROUTING_DB_PORT", str(pg.get_exposed_port(5432)))
        monkeypatch_module.setenv("ROUTING_DB_USER", "routing")
        monkeypatch_module.setenv("ROUTING_DB_PASSWORD", "routing_test")
        monkeypatch_module.setenv("ROUTING_DB_NAME", "routing")

        from open_routing_service.config import get_settings
        get_settings.cache_clear()

        yield _async_url(pg)

    from open_routing_service.config import get_settings
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def live_redis(monkeypatch_module: pytest.MonkeyPatch) -> Generator[str, None, None]:  # type: ignore[name-defined]
    """Provide a live Redis URL.

    Priority:
      1. If REDIS_URL is already set, use it (dev compose stack).
      2. Otherwise boot testcontainers Redis.
    """
    pre_configured = os.environ.get("REDIS_URL")
    if pre_configured:
        yield pre_configured
        return

    if not DOCKER:
        pytest.skip("Docker not available and no REDIS_URL set")

    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as redis:
        url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        monkeypatch_module.setenv("REDIS_URL", url)
        yield url


# --------------------------------------------------------------------------- #
# Alembic fixture
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def apply_alembic(live_db: str, monkeypatch_module: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    """Run alembic upgrade head against the live_db container."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_SERVICE_ROOT / "alembic"))
    command.upgrade(cfg, "head")


# --------------------------------------------------------------------------- #
# module-scoped monkeypatch (pytest built-in is function-scoped)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def monkeypatch_module() -> Generator[pytest.MonkeyPatch, None, None]:
    """Module-scoped monkeypatch so container env vars survive across tests."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run_init_sql(pg: "PostgresContainer") -> None:  # type: ignore[name-defined]
    """Create postgis + pgrouting extensions in the container DB."""
    import psycopg2
    with psycopg2.connect(
        host=pg.get_container_host_ip(),
        port=pg.get_exposed_port(5432),
        user="routing",
        password="routing_test",
        dbname="routing",
    ) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis_topology;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgrouting;")


def _async_url(pg: "PostgresContainer") -> str:  # type: ignore[name-defined]
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    return f"postgresql+asyncpg://routing:routing_test@{host}:{port}/routing"


def _sync_url(pg: "PostgresContainer") -> str:  # type: ignore[name-defined]
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    return f"postgresql://routing:routing_test@{host}:{port}/routing"
