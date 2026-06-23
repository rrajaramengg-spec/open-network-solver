"""Tests for the Alembic baseline migration ``20260620_0001``.

Two tiers:
  1. **Structural tests** — pure-Python introspection of the migration module
     and the SQLAlchemy model metadata. Always run; no Docker, no DB.
  2. **Round-trip tests** — boot a throwaway Postgres via ``testcontainers``,
     run ``alembic upgrade head`` then ``downgrade base``, and assert table
     existence at each step. Skipped cleanly when Docker is unavailable.

Implements Phase 1 task 1.10: "Unit tests for the ``function_version`` Alembic
baseline (round-trip migration up/down on a throwaway in-memory or container
DB)."
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover
    from types import ModuleType

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _SERVICE_ROOT
    / "alembic"
    / "versions"
    / "20260620_0001_baseline_etl_runs_function_version.py"
)
_ALEMBIC_INI = _SERVICE_ROOT / "alembic.ini"


@pytest.fixture(scope="module")
def baseline_module() -> "ModuleType":
    """Import the baseline migration file as a Python module."""
    spec = importlib.util.spec_from_file_location(
        "baseline_etl_runs_function_version", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 1. Structural tests (always run)
# --------------------------------------------------------------------------- #


class TestMigrationStructure:
    """Assert the migration module exports the right shape."""

    def test_revision_id_matches_filename(self, baseline_module: "ModuleType") -> None:
        assert baseline_module.revision == "20260620_0001"

    def test_is_baseline_revision(self, baseline_module: "ModuleType") -> None:
        assert baseline_module.down_revision is None

    def test_no_branch_label(self, baseline_module: "ModuleType") -> None:
        assert baseline_module.branch_labels is None

    def test_upgrade_and_downgrade_are_callable(self, baseline_module: "ModuleType") -> None:
        assert callable(baseline_module.upgrade)
        assert callable(baseline_module.downgrade)

    def test_upgrade_creates_both_tables(self, baseline_module: "ModuleType") -> None:
        """Read-only inspection: the migration source mentions both tables."""
        src = _MIGRATION_PATH.read_text(encoding="utf-8")
        assert 'op.create_table(\n        "etl_runs"' in src
        assert 'op.create_table(\n        "function_version"' in src

    def test_function_version_seeds_v1_row(self, baseline_module: "ModuleType") -> None:
        src = _MIGRATION_PATH.read_text(encoding="utf-8")
        assert "INSERT INTO function_version (id, version) VALUES (1, 'v1')" in src

    def test_downgrade_drops_in_reverse_order(self, baseline_module: "ModuleType") -> None:
        """function_version was created last so it must be dropped first."""
        src = _MIGRATION_PATH.read_text(encoding="utf-8")
        downgrade_src = src.split("def downgrade")[1]
        # function_version drop appears BEFORE etl_runs drop
        fv_idx = downgrade_src.find('drop_table("function_version")')
        er_idx = downgrade_src.find('drop_table("etl_runs")')
        assert 0 <= fv_idx < er_idx


class TestModelMetadataMatchesMigration:
    """Verify the SQLAlchemy ORM models declare the same columns the migration creates."""

    def test_etl_runs_columns(self) -> None:
        from open_routing_service.models import Base
        # Importing the models package registers EtlRun + FunctionVersion on Base.metadata
        tbl = Base.metadata.tables["etl_runs"]
        names = {c.name for c in tbl.columns}
        assert names == {
            "id",
            "pbf_filename",
            "pbf_sha256",
            "pbf_published_date",
            "started_at",
            "completed_at",
            "ways_count",
            "vertices_count",
        }
        # sha256 unique constraint enforces idempotency at the DB layer
        uniques = {c.name for c in tbl.constraints if c.__class__.__name__ == "UniqueConstraint"}
        assert "uq_etl_runs_pbf_sha256" in uniques

    def test_function_version_singleton_check(self) -> None:
        from open_routing_service.models import Base
        tbl = Base.metadata.tables["function_version"]
        checks = [c for c in tbl.constraints if c.__class__.__name__ == "CheckConstraint"]
        # The model declares a CheckConstraint pinning id=1
        assert any(c.name == "ck_function_version_singleton" for c in checks)
        # Same column set the migration creates
        names = {c.name for c in tbl.columns}
        assert names == {"id", "version", "updated_at"}


# --------------------------------------------------------------------------- #
# 2. Round-trip tests (Postgres; gated on the dev compose stack being reachable)
# --------------------------------------------------------------------------- #


def _dev_stack_available() -> bool:
    """True if the local dev Postgres (compose stack) is reachable.

    We prefer running migration round-trips against the dev stack instead of
    spinning up a testcontainers Postgres because:

    1. The dev stack is already running for integration tests; reusing it cuts
       ~10 s per test.
    2. Windows + ``testcontainers`` + pytest module-scoped contextmanager
       teardown hangs intermittently waiting for the cleanup of the container
       (observed reliably during Phase 1). The dev stack avoids that path
       entirely.

    The test runs the round-trip in a **disposable database** created and
    dropped from the maintenance ``routing`` database, so the live schema is
    never touched.
    """
    import os

    if not all(
        os.environ.get(k)
        for k in ("ROUTING_DB_HOST", "ROUTING_DB_PORT", "ROUTING_DB_USER", "ROUTING_DB_PASSWORD")
    ):
        return False
    try:
        import psycopg2  # type: ignore[import-not-found]
        conn = psycopg2.connect(
            host=os.environ["ROUTING_DB_HOST"],
            port=int(os.environ["ROUTING_DB_PORT"]),
            user=os.environ["ROUTING_DB_USER"],
            password=os.environ["ROUTING_DB_PASSWORD"],
            dbname=os.environ.get("ROUTING_DB_NAME", "routing"),
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:  # noqa: BLE001 — any failure means we skip
        return False


_DEV_STACK = _dev_stack_available()


@pytest.fixture(scope="module")
def postgres_url() -> str:
    """Create a disposable database in the dev Postgres and return its URL.

    The database name is unique per test session so concurrent runs don't
    collide. The fixture cleans up by ``DROP DATABASE`` in teardown.

    Returns a ``postgresql+psycopg2://`` URL that env.py (sync Alembic engine)
    can consume directly.
    """
    if not _DEV_STACK:
        pytest.skip(
            "Dev Postgres stack not available; set ROUTING_DB_HOST/PORT/USER/PASSWORD "
            "to a reachable Postgres to run the migration round-trip."
        )

    import os
    import uuid
    import psycopg2  # type: ignore[import-not-found]
    from psycopg2 import sql

    host = os.environ["ROUTING_DB_HOST"]
    port = int(os.environ["ROUTING_DB_PORT"])
    user = os.environ["ROUTING_DB_USER"]
    password = os.environ["ROUTING_DB_PASSWORD"]
    maint_db = os.environ.get("ROUTING_DB_NAME", "routing")

    test_db = f"alembic_roundtrip_{uuid.uuid4().hex[:8]}"

    # CREATE / DROP DATABASE cannot run inside a transaction.
    admin = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=maint_db)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db)))
    finally:
        admin.close()

    yield f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{test_db}"

    # Teardown: forcefully terminate any leftover connections, then drop.
    admin = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=maint_db)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (test_db,),
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(test_db)))
    finally:
        admin.close()


@pytest.mark.skipif(not _DEV_STACK, reason="Dev Postgres stack not available")
class TestMigrationRoundTrip:
    """Run ``alembic upgrade head`` then ``downgrade base`` against real Postgres."""

    def test_upgrade_then_downgrade_is_clean(
        self,
        postgres_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from alembic import command
        from alembic.config import Config

        # Wire our settings to the disposable DB so env.py picks it up.
        monkeypatch.setenv("ROUTING_DB_HOST", _hostport(postgres_url)[0])
        monkeypatch.setenv("ROUTING_DB_PORT", str(_hostport(postgres_url)[1]))
        monkeypatch.setenv("ROUTING_DB_USER", _user_pass(postgres_url)[0])
        monkeypatch.setenv("ROUTING_DB_PASSWORD", _user_pass(postgres_url)[1])
        monkeypatch.setenv("ROUTING_DB_NAME", _dbname(postgres_url))
        from open_routing_service.config import get_settings
        get_settings.cache_clear()  # force re-read of env

        cfg = Config(str(_ALEMBIC_INI))
        cfg.set_main_option("script_location", str(_SERVICE_ROOT / "alembic"))

        # Upgrade to head
        command.upgrade(cfg, "head")

        # Verify with a quick sync connection.
        import psycopg2  # type: ignore[import-not-found]
        with psycopg2.connect(
            host=_hostport(postgres_url)[0],
            port=_hostport(postgres_url)[1],
            user=_user_pass(postgres_url)[0],
            password=_user_pass(postgres_url)[1],
            dbname=_dbname(postgres_url),
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN "
                    "('etl_runs','function_version')"
                )
                names = {row[0] for row in cur.fetchall()}
            assert names == {"etl_runs", "function_version"}, names

            # function_version was seeded with id=1, version='v1'
            with conn.cursor() as cur:
                cur.execute("SELECT id, version FROM function_version")
                rows = cur.fetchall()
            assert rows == [(1, "v1")]

            # Downgrade
            command.downgrade(cfg, "base")

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN "
                    "('etl_runs','function_version')"
                )
                names_after = {row[0] for row in cur.fetchall()}
            assert names_after == set(), f"tables still present after downgrade: {names_after}"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _hostport(url: str) -> tuple[str, int]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 5432


def _user_pass(url: str) -> tuple[str, str]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.username or "", parsed.password or ""


def _dbname(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return (parsed.path or "/postgres").lstrip("/")


# --------------------------------------------------------------------------- #
# 3. Phase 2 migration structural tests (always run — no DB required)
# --------------------------------------------------------------------------- #

_PHASE2_MIGRATIONS = [
    (
        "20260622_0001_phase2_facilities.py",
        "20260622_0001",
        "20260620_0001",
    ),
    (
        "20260622_0002_phase2_cost_columns.py",
        "20260622_0002",
        "20260622_0001",
    ),
    (
        "20260622_0003_phase2_closest_facility.py",
        "20260622_0003",
        "20260622_0002",
    ),
]


class TestPhase2MigrationStructure:
    """Static checks on Phase 2 migration modules — always run, no DB."""

    @pytest.mark.parametrize("filename,revision,down_revision", _PHASE2_MIGRATIONS)
    def test_revision_and_chain(
        self, filename: str, revision: str, down_revision: str
    ) -> None:
        migration_path = _SERVICE_ROOT / "alembic" / "versions" / filename
        spec = importlib.util.spec_from_file_location(revision, migration_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.revision == revision
        assert module.down_revision == down_revision
        assert callable(module.upgrade)
        assert callable(module.downgrade)

    def test_closest_facility_migration_bumps_function_version(self) -> None:
        filename = "20260622_0003_phase2_closest_facility.py"
        migration_path = _SERVICE_ROOT / "alembic" / "versions" / filename
        spec = importlib.util.spec_from_file_location("m", migration_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert "v2" in mod._BUMP_VERSION_SQL
        assert "pgr_dijkstra" in mod._FUNCTION_SQL
        assert "incident_off_graph" in mod._FUNCTION_SQL

    def test_cost_columns_migration_is_idempotent(self) -> None:
        filename = "20260622_0002_phase2_cost_columns.py"
        migration_path = _SERVICE_ROOT / "alembic" / "versions" / filename
        spec = importlib.util.spec_from_file_location("m", migration_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Idempotency is guaranteed by ADD COLUMN IF NOT EXISTS
        assert "IF NOT EXISTS" in mod._ADD_COLUMNS_SQL
        assert "cost_distance" in mod._ADD_COLUMNS_SQL
        assert "cost_time" in mod._ADD_COLUMNS_SQL