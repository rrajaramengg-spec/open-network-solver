"""Integration test 1.11 — full Phase 1 ETL pipeline (spec-driven acceptance).

Implements Phase 1 tasks 1.11–1.13.

Tasks 1.11 (happy path) and 1.12 (staging-schema invariant) verify the
post-ETL state in a live Postgres (the infra/docker-compose.yml dev stack)
where the ETL has already loaded the Andorra fixture:

  (a) routing schema exists
  (b) swap committed (routing.ways and routing.ways_vertices_pgr non-empty)
  (c) pgr_dijkstra returns a row between two connected vertices
  (d) etl_runs has a provenance row with non-zero ways_count
  (e) staging-schema invariant: creating an empty routing_next schema while
      routing is live does NOT affect routing's query results
  (f) idempotent re-run via docker compose short-circuits cleanly

Task 1.13 (failure path) runs the orchestrator via ``docker compose run``
with a path that does not exist and asserts the live schema and etl_runs
row count are unchanged.

The ETL itself is exercised by ``docker compose --profile etl run --rm etl``
which IS the production code path — we deliberately do NOT import the ETL
module directly to avoid host-side dependencies
(osmconvert / osm2pgrouting / psql binaries).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg2
import pytest

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_PBF_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "etl-small"
_FIXTURE_PBFS = sorted(_FIXTURE_PBF_DIR.glob("*.osm.pbf")) if _FIXTURE_PBF_DIR.exists() else []

_COMPOSE_ENV_FILE = _REPO_ROOT / "infra" / ".env"
_COMPOSE_FILE = _REPO_ROOT / "infra" / "docker-compose.yml"


def _docker_available() -> bool:
    """Return True if docker daemon is reachable."""
    try:
        out = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        )
        return out.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


DOCKER = _docker_available()
DEV_STACK_CONFIGURED = all(
    os.environ.get(k)
    for k in (
        "ROUTING_DB_HOST", "ROUTING_DB_PORT", "ROUTING_DB_USER",
        "ROUTING_DB_PASSWORD", "ROUTING_DB_NAME",
    )
)


def _skip_if_no_fixture() -> None:
    if not _FIXTURE_PBFS:
        pytest.skip(
            "No fixture PBF found under tests/fixtures/etl-small/. "
            "Commit a small PBF first (e.g. andorra-latest.osm.pbf)."
        )


def _skip_if_no_dev_stack() -> None:
    if not DEV_STACK_CONFIGURED:
        pytest.skip(
            "ROUTING_DB_* env vars not set — these integration tests require "
            "the dev compose stack. "
            "Boot it with: docker compose --env-file infra/.env -f infra/docker-compose.yml up -d"
        )


def _conn() -> psycopg2.extensions.connection:
    """Connect to the live dev-stack Postgres."""
    return psycopg2.connect(
        host=os.environ["ROUTING_DB_HOST"],
        port=int(os.environ["ROUTING_DB_PORT"]),
        user=os.environ["ROUTING_DB_USER"],
        password=os.environ["ROUTING_DB_PASSWORD"],
        dbname=os.environ["ROUTING_DB_NAME"],
    )


def _run_etl_via_compose(*extra_args: str) -> subprocess.CompletedProcess[str]:
    """Run the ETL container via docker compose; return CompletedProcess."""
    cmd = [
        "docker", "compose",
        "--env-file", str(_COMPOSE_ENV_FILE),
        "-f", str(_COMPOSE_FILE),
        "--profile", "etl",
        "run", "--rm", "etl", *extra_args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


# --------------------------------------------------------------------------- #
# Happy-path verification (task 1.11)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not DOCKER, reason="Docker not available")
class TestEtlPipelineHappyPath:
    """Verify post-ETL state matches Phase 1 acceptance criteria.

    Precondition: the dev stack has completed at least one Andorra fixture ETL.
    """

    @classmethod
    def setup_class(cls) -> None:
        _skip_if_no_fixture()
        _skip_if_no_dev_stack()

    def test_routing_schema_exists_and_populated(self) -> None:
        """(a)(b) swap committed → routing schema has ways+vertices."""
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'routing'"
            )
            assert cur.fetchone() is not None, (
                "live 'routing' schema missing — has the ETL been run? "
                "Try: docker compose --env-file infra/.env -f infra/docker-compose.yml "
                "--profile etl run --rm etl --pbf /data/osm/andorra-latest.osm.pbf"
            )

            cur.execute("SELECT count(*) FROM routing.ways")
            ways = cur.fetchone()[0]
            assert ways > 0, f"routing.ways is empty (count={ways})"

            cur.execute("SELECT count(*) FROM routing.ways_vertices_pgr")
            verts = cur.fetchone()[0]
            assert verts > 0, f"routing.ways_vertices_pgr is empty (count={verts})"

    def test_pgr_dijkstra_returns_a_route(self) -> None:
        """(c) pgr_dijkstra between two connected vertices returns >=1 row."""
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, target FROM routing.ways
                WHERE source IS NOT NULL AND target IS NOT NULL AND source != target
                LIMIT 1
                """
            )
            row = cur.fetchone()
            assert row is not None, "no edges found in routing.ways"
            src, tgt = row

            cur.execute(
                """
                SELECT count(*) FROM pgr_dijkstra(
                    'SELECT gid AS id, source, target, cost_distance AS cost FROM routing.ways',
                    %s, %s, directed := false
                )
                """,
                (src, tgt),
            )
            n = cur.fetchone()[0]
            assert n > 0, f"pgr_dijkstra({src},{tgt}) returned 0 rows"

    def test_etl_runs_provenance_row_inserted(self) -> None:
        """(d) etl_runs has the success row from the ETL."""
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pbf_filename, ways_count, vertices_count, pbf_sha256 "
                "FROM etl_runs ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            assert row is not None, "etl_runs is empty after successful ETL"
            filename, ways, verts, sha = row
            assert filename.endswith(".osm.pbf"), f"unexpected pbf_filename: {filename!r}"
            assert ways > 0, f"etl_runs.ways_count is {ways}"
            assert verts > 0, f"etl_runs.vertices_count is {verts}"
            assert len(sha) == 64, f"unexpected sha256 length: {len(sha)}"

    def test_idempotent_rerun_via_compose(self) -> None:
        """(f) Re-running ETL with the same PBF short-circuits cleanly."""
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM etl_runs")
            count_before = cur.fetchone()[0]

        fixture = _FIXTURE_PBFS[0]
        proc = _run_etl_via_compose("--pbf", f"/data/osm/{fixture.name}")
        assert proc.returncode == 0, (
            f"idempotent re-run failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}"
        )
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM etl_runs")
            count_after = cur.fetchone()[0]
        assert count_after == count_before, (
            f"idempotent rerun inserted a new etl_runs row "
            f"(before={count_before}, after={count_after})"
        )


# --------------------------------------------------------------------------- #
# Staging-schema invariant (task 1.12)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not DOCKER, reason="Docker not available")
class TestEtlStagingSchemaInvariant:
    """Spec: 'ETL writes to staging schema, never to live schema'."""

    @classmethod
    def setup_class(cls) -> None:
        _skip_if_no_fixture()
        _skip_if_no_dev_stack()

    def test_staging_schema_creation_does_not_affect_live_routing(self) -> None:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM routing.ways")
            before = cur.fetchone()[0]
            assert before > 0, "precondition: routing.ways must be populated"

        with _conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS routing_next CASCADE")
                cur.execute("CREATE SCHEMA routing_next")

        try:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM routing.ways")
                during = cur.fetchone()[0]
                assert during == before, (
                    f"routing.ways changed while routing_next exists "
                    f"(before={before}, during={during})"
                )
        finally:
            with _conn() as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("DROP SCHEMA IF EXISTS routing_next CASCADE")


# --------------------------------------------------------------------------- #
# Failure-path invariant (task 1.13)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not DOCKER, reason="Docker not available")
class TestEtlFailureLeavesLiveSchemaIntact:
    """Spec: 'ETL failure leaves live schema untouched'."""

    @classmethod
    def setup_class(cls) -> None:
        _skip_if_no_dev_stack()

    def test_etl_failure_leaves_routing_schema_and_etl_runs_unchanged(self) -> None:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM routing.ways")
            ways_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM etl_runs")
            runs_before = cur.fetchone()[0]

        proc = _run_etl_via_compose("--pbf", "/data/osm/__nonexistent__.osm.pbf")
        assert proc.returncode != 0, (
            f"ETL should fail with non-existent PBF but exited 0:\n{proc.stdout}"
        )

        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM routing.ways")
            ways_after = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM etl_runs")
            runs_after = cur.fetchone()[0]

        assert ways_after == ways_before, (
            f"routing.ways changed after failed ETL "
            f"(before={ways_before}, after={ways_after})"
        )
        assert runs_after == runs_before, (
            f"etl_runs row count changed after failed ETL "
            f"(before={runs_before}, after={runs_after})"
        )
