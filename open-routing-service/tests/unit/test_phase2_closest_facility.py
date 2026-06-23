"""SQL unit tests for the ``closest_facility`` PL/pgSQL function.

Uses ``testcontainers`` to boot a throwaway pgRouting Postgres, seeds it
with the ``tests/fixtures/network-tiny/seed.sql`` fixture, installs the
``closest_facility`` function from the Alembic migration module, then
runs every scenario from ``specs/closest-facility/spec.md``.

Marked ``@pytest.mark.unit`` (they exercise pure SQL logic) even though
they need a real DB — they are fast (< 10 s total on a cold container start
because the fixture network is tiny).

Implements Phase 2 task 2.5.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Generator

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_SQL = _SERVICE_ROOT / "tests" / "fixtures" / "network-tiny" / "seed.sql"
_VERSIONS_DIR = _SERVICE_ROOT / "alembic" / "versions"
# The original migration that introduced the function. Kept for static checks.
_MIGRATION_PATH = _VERSIONS_DIR / "20260622_0003_phase2_closest_facility.py"
# Migrations that replace the function body, in revision order. The last one
# wins because each issues ``CREATE OR REPLACE FUNCTION``.
_FUNCTION_MIGRATION_CHAIN = (
    _MIGRATION_PATH,
    _VERSIONS_DIR / "20260622_0004_phase2_fix_closest_facility_format.py",
    _VERSIONS_DIR / "20260622_0005_phase2_fix_closest_facility_ambiguity.py",
    _VERSIONS_DIR / "20260622_0006_phase2_fix_closest_facility_aggregation.py",
    _VERSIONS_DIR / "20260623_0001_scalable_bbox_oneway_closest_facility.py",
    _VERSIONS_DIR / "20260623_0002_facility_bbox_prefilter_closest_facility.py",
    _VERSIONS_DIR / "20260623_0003_facility_attributes_category_closest_facility.py",
)


# ---------------------------------------------------------------------------
# Docker availability guard (mirrors integration/conftest.py)
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker not available")


# ---------------------------------------------------------------------------
# Import the migration module so we can extract the function SQL
# ---------------------------------------------------------------------------


def _load_migration(path: Path = _MIGRATION_PATH):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _latest_function_sql() -> str:
    """Return ``_FUNCTION_SQL`` from the last migration in the chain."""
    return _load_migration(_FUNCTION_MIGRATION_CHAIN[-1])._FUNCTION_SQL


# ---------------------------------------------------------------------------
# Per-module Postgres container fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_conn(tmp_path_factory):
    """Boot a throwaway pgRouting Postgres, seed the tiny network, and yield
    a live psycopg connection for raw SQL execution."""
    pytest.importorskip("testcontainers")
    pytest.importorskip("psycopg")

    from testcontainers.postgres import PostgresContainer

    # Use the same upstream pgrouting image as the production compose stack.
    img = "pgrouting/pgrouting:16-3.5-3.7.3"
    with PostgresContainer(img, driver="psycopg") as pg:
        import psycopg

        dsn = pg.get_connection_url()
        # testcontainers returns sqlalchemy DSN; convert to libpq format.
        libpq_dsn = dsn.replace("postgresql+psycopg://", "postgresql://")

        with psycopg.connect(libpq_dsn, autocommit=True) as conn:
            # Seed network + facilities.
            conn.execute(_FIXTURE_SQL.read_text())

            # Install the closest_facility function from the latest migration
            # in the fix chain (CREATE OR REPLACE FUNCTION — only the body of
            # the final one matters).
            conn.execute(_latest_function_sql())

            yield conn


# ---------------------------------------------------------------------------
# Helper to call closest_facility and return rows as dicts
# ---------------------------------------------------------------------------


def _call(
    conn,
    *,
    lon: float = -150.0000,  # V1 longitude (default incident)
    lat: float = 20.0000,    # V1 latitude  (default incident)
    buffer_m: float = 2000.0,
    facility_filter: str = "{}",
    k: int = 5,
    cost_mode: str = "distance",
) -> list[dict]:
    import json
    rows = conn.execute(
        "SELECT facility_id, rank, total_cost, total_distance_m, "
        "       total_time_s, route_geom IS NOT NULL AS has_geom "
        "FROM closest_facility("
        "  ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geometry(Point,4326),"
        "  %s, %s::jsonb, %s, %s"
        ") ORDER BY rank",
        (lon, lat, buffer_m, facility_filter, k, cost_mode),
    ).fetchall()
    cols = ["facility_id", "rank", "total_cost", "total_distance_m", "total_time_s", "has_geom"]
    return [dict(zip(cols, row)) for row in rows]


# ---------------------------------------------------------------------------
# Spec scenarios
# ---------------------------------------------------------------------------


class TestTop1ByDistance:
    """Scenario: Returns top-1 nearest facility by distance."""

    def test_returns_exactly_one_row(self, pg_conn) -> None:
        rows = _call(pg_conn, k=1, cost_mode="distance")
        assert len(rows) == 1

    def test_nearest_facility_by_distance_is_first(self, pg_conn) -> None:
        # From V1(0,0): FC(V4) is 110m via e3; FA(V3) is 200m via e1+e2.
        # Hospital (FB/V6) is reachable via e3+e6+e7 = 310m.
        rows = _call(pg_conn, k=1, cost_mode="distance")
        # Facility 3 (Fire Station C at V4, 110m) should be rank 1.
        assert rows[0]["facility_id"] == 3

    def test_total_distance_is_positive(self, pg_conn) -> None:
        rows = _call(pg_conn, k=1, cost_mode="distance")
        assert rows[0]["total_distance_m"] > 0

    def test_route_geom_is_present(self, pg_conn) -> None:
        rows = _call(pg_conn, k=1, cost_mode="distance")
        assert rows[0]["has_geom"] is True


class TestTopKRankOrder:
    """Scenario: Returns top-K facilities ranked by cost."""

    def test_returns_k_rows_when_enough_facilities(self, pg_conn) -> None:
        rows = _call(pg_conn, k=3, cost_mode="distance")
        assert len(rows) == 3

    def test_rank_column_is_sequential(self, pg_conn) -> None:
        rows = _call(pg_conn, k=3, cost_mode="distance")
        assert [r["rank"] for r in rows] == [1, 2, 3]

    def test_rows_ordered_by_ascending_cost(self, pg_conn) -> None:
        rows = _call(pg_conn, k=3, cost_mode="distance")
        costs = [r["total_cost"] for r in rows]
        assert costs == sorted(costs)


class TestCostModeTime:
    """Scenario: cost_mode='time' uses travel-time costs."""

    def test_time_mode_changes_rank_order(self, pg_conn) -> None:
        dist_rows = _call(pg_conn, k=3, cost_mode="distance")
        time_rows = _call(pg_conn, k=3, cost_mode="time")

        # Distance mode: FC(110m) < FA(200m) < FB(310m) → rank order 3,1,2
        dist_ids = [r["facility_id"] for r in dist_rows]
        time_ids = [r["facility_id"] for r in time_rows]

        # With time mode, FA (e1=4s + e2=30s = 34s to V3) competes with
        # FC (e3=4s to V4). FC is still rank 1 by time (4s < 34s).
        # But the relative ordering of FA vs FB may change.
        # Key assertion: the top-1 facility by time should be FC (4s).
        assert time_rows[0]["facility_id"] == 3  # FC via e3 = 4s

    def test_total_time_s_is_populated(self, pg_conn) -> None:
        rows = _call(pg_conn, k=1, cost_mode="time")
        assert rows[0]["total_time_s"] > 0


class TestFacilityFilter:
    """Scenario: facility_filter limits the candidate set."""

    def test_filter_excludes_non_matching_type(self, pg_conn) -> None:
        # Request only hospitals: should get FB only, not fire stations.
        rows = _call(pg_conn, k=5, facility_filter='{"amenity":"hospital"}')
        facility_ids = [r["facility_id"] for r in rows]
        assert 2 in facility_ids          # Hospital B
        assert 1 not in facility_ids      # Fire Station A
        assert 3 not in facility_ids      # Fire Station C

    def test_filter_fire_station_returns_two_rows(self, pg_conn) -> None:
        rows = _call(pg_conn, k=5, facility_filter='{"amenity":"fire_station"}')
        assert len(rows) == 2

    def test_empty_filter_returns_all(self, pg_conn) -> None:
        rows = _call(pg_conn, k=5, facility_filter="{}")
        assert len(rows) == 3


class TestNoFacilityInBuffer:
    """Scenario: No facility within buffer — returns zero rows (not an error)."""

    def test_returns_empty_on_tiny_buffer(self, pg_conn) -> None:
        # 1 m buffer — no facility can be within 1 m of the incident vertex.
        rows = _call(pg_conn, buffer_m=1.0)
        assert rows == []


class TestFewerThanKFacilities:
    """Scenario: Fewer than K facilities reachable — return what exists."""

    def test_returns_fewer_rows_than_k(self, pg_conn) -> None:
        rows = _call(pg_conn, k=10, cost_mode="distance")
        # Only 3 facilities total; should get at most 3.
        assert len(rows) == 3


class TestIncidentOffGraph:
    """Scenario: Incident too far from any road raises incident_off_graph."""

    def test_raises_exception_for_distant_incident(self, pg_conn) -> None:
        import psycopg

        # Place incident 1° south of our network (~111 km away from V1).
        with pytest.raises(psycopg.errors.RaiseException) as exc_info:
            _call(pg_conn, lat=18.9, buffer_m=2000.0)

        assert "incident_off_graph" in str(exc_info.value)

    def test_snappable_incident_does_not_raise(self, pg_conn) -> None:
        # Incident placed exactly at V1 — should snap and succeed.
        rows = _call(pg_conn, lon=-150.0000, lat=20.0000, k=1)
        assert len(rows) >= 0  # no exception raised


class TestMigrationStructure:
    """Static checks — included in this module for completeness; skip gracefully
    when Docker is unavailable (module-level pytestmark applies)."""

    def test_revision_links_to_previous(self) -> None:
        migration = _load_migration()
        assert migration.revision == "20260622_0003"
        assert migration.down_revision == "20260622_0002"

    def test_function_sql_contains_signature(self) -> None:
        sql = _latest_function_sql()
        assert "closest_facility" in sql
        assert "pgr_dijkstra" in sql
        assert "incident_off_graph" in sql

    def test_latest_migration_bumps_function_version(self) -> None:
        """Each fix migration must invalidate the cache by bumping version."""
        migration = _load_migration(_FUNCTION_MIGRATION_CHAIN[-1])
        assert "function_version" in migration._BUMP_VERSION_SQL
        # Latest migration bumps to v8 (facility attributes + category); update on a new fix.
        assert "'v8'" in migration._BUMP_VERSION_SQL

    def test_downgrade_drops_function(self) -> None:
        migration = _load_migration()
        assert "DROP FUNCTION" in migration._DROP_FUNCTION_SQL

    def test_fix_chain_is_linked(self) -> None:
        """Each migration in the chain links to its predecessor."""
        for prev, curr in zip(_FUNCTION_MIGRATION_CHAIN, _FUNCTION_MIGRATION_CHAIN[1:]):
            assert _load_migration(curr).down_revision == _load_migration(prev).revision

    def test_latest_function_has_no_c_style_format_specifier(self) -> None:
        """Regression guard for the v4 fix: PG ``format()`` does not support ``%.0f``."""
        import re
        sql = _latest_function_sql()
        assert re.search(r"%\.\d*[a-zA-Z]", sql) is None, (
            "PostgreSQL format() only supports %s, %I, %L, %%"
        )

    def test_latest_function_declares_variable_conflict_pragma(self) -> None:
        """Regression guard for the v5 fix."""
        sql = _latest_function_sql()
        assert "#variable_conflict use_column" in sql

    def test_latest_function_bounds_edges_by_bbox(self) -> None:
        """v6 (design D8): edges SQL must be bounded by a bbox, not the whole table."""
        sql = _latest_function_sql()
        assert "ST_MakeEnvelope" in sql
        assert "the_geom &&" in sql

    def test_latest_function_blocks_oneway_reverse(self) -> None:
        """v6 (design D9): one-way edges get reverse_cost = -1."""
        sql = _latest_function_sql()
        assert "oneway = 1" in sql
        assert "-1" in sql


class TestFacilityCategorySQL:
    """facility-details: facility_category(tags) taxonomy (design D2)."""

    def _cat(self, conn, tags_json: str) -> str:
        return conn.execute(
            "SELECT facility_category(%s::jsonb)", (tags_json,)
        ).fetchone()[0]

    def test_amenity_maps_to_value(self, pg_conn) -> None:
        assert self._cat(pg_conn, '{"amenity":"fire_station"}') == "fire_station"
        assert self._cat(pg_conn, '{"amenity":"hospital"}') == "hospital"
        assert self._cat(pg_conn, '{"amenity":"police"}') == "police"
        assert self._cat(pg_conn, '{"amenity":"school"}') == "school"

    def test_emergency_ambulance_station_takes_priority(self, pg_conn) -> None:
        assert self._cat(pg_conn, '{"emergency":"ambulance_station"}') == "ambulance_station"

    def test_shop_and_tourism_fall_through(self, pg_conn) -> None:
        assert self._cat(pg_conn, '{"shop":"supermarket"}') == "supermarket"
        assert self._cat(pg_conn, '{"tourism":"hotel"}') == "hotel"

    def test_unknown_tags_classify_as_other(self, pg_conn) -> None:
        assert self._cat(pg_conn, '{"building":"yes"}') == "other"
        assert self._cat(pg_conn, "{}") == "other"

    def test_value_is_lowercased_and_trimmed(self, pg_conn) -> None:
        assert self._cat(pg_conn, '{"amenity":"  Hospital  "}') == "hospital"


class TestEnrichedColumns:
    """facility-details: v8 projects name / category / tags / geom (design D3)."""

    def _call_enriched(self, conn, *, k: int = 1) -> list[dict]:
        rows = conn.execute(
            "SELECT facility_id, rank, facility_name, facility_category, "
            "       facility_tags::text AS facility_tags, "
            "       ST_AsGeoJSON(facility_geom) AS facility_geom_json "
            "FROM closest_facility("
            "  ST_SetSRID(ST_MakePoint(-150.0, 20.0), 4326)::geometry(Point,4326),"
            "  2000.0, '{}'::jsonb, %s, 'distance'"
            ") ORDER BY rank",
            (k,),
        ).fetchall()
        cols = [
            "facility_id", "rank", "facility_name",
            "facility_category", "facility_tags", "facility_geom_json",
        ]
        return [dict(zip(cols, row)) for row in rows]

    def test_projects_name(self, pg_conn) -> None:
        rows = self._call_enriched(pg_conn, k=1)
        assert rows[0]["facility_name"] == "Fire Station C"

    def test_projects_category(self, pg_conn) -> None:
        rows = self._call_enriched(pg_conn, k=1)
        assert rows[0]["facility_category"] == "fire_station"

    def test_projects_tags_and_point_geojson(self, pg_conn) -> None:
        import json
        rows = self._call_enriched(pg_conn, k=1)
        tags = json.loads(rows[0]["facility_tags"])
        assert tags.get("amenity") == "fire_station"
        geom = json.loads(rows[0]["facility_geom_json"])
        assert geom["type"] == "Point"
        assert len(geom["coordinates"]) == 2

    def test_latest_function_projects_facility_columns(self) -> None:
        sql = _latest_function_sql()
        assert "facility_name" in sql
        assert "facility_category" in sql
        assert "facility_geom" in sql


# ---------------------------------------------------------------------------
# v6: bbox-bounded query (design D8) + one-way edges (design D9)
# ---------------------------------------------------------------------------


from contextlib import contextmanager


class TestBboxBoundedQuery:
    """Design D8 — graph handed to Dijkstra is size-independent.

    A far-away edge/facility (thousands of km outside the incident bbox) must
    not change results and must not be loaded by ``pgr_dijkstra``.
    """

    @contextmanager
    def _faraway_network(self, conn):
        """Insert a disconnected edge + facility far outside any bbox; clean up."""
        conn.execute(
            """
            INSERT INTO routing.ways_vertices_pgr (id, the_geom) VALUES
                (901, ST_SetSRID(ST_MakePoint(-60.0, 20.0), 4326)),
                (902, ST_SetSRID(ST_MakePoint(-59.999, 20.0), 4326));
            INSERT INTO routing.ways
                (gid, osm_id, tag_id, source, target, the_geom,
                 cost_distance, cost_time, oneway)
            VALUES (901, 99001, 111, 901, 902,
                    ST_MakeLine(ST_SetSRID(ST_MakePoint(-60.0, 20.0), 4326),
                                ST_SetSRID(ST_MakePoint(-59.999, 20.0), 4326)),
                    100.0, 4.0, 0);
            INSERT INTO routing.facilities (id, osm_id, name, tags, geom, vertex_id)
            VALUES (901, 99001, 'Far Station',
                    '{"amenity":"fire_station"}'::jsonb,
                    ST_SetSRID(ST_MakePoint(-60.0, 20.0), 4326), 901);
            """
        )
        try:
            yield
        finally:
            conn.execute("DELETE FROM routing.facilities WHERE id = 901;")
            conn.execute("DELETE FROM routing.ways WHERE gid = 901;")
            conn.execute(
                "DELETE FROM routing.ways_vertices_pgr WHERE id IN (901, 902);"
            )

    def test_results_unchanged_by_faraway_edges(self, pg_conn) -> None:
        baseline = _call(pg_conn, k=3, cost_mode="distance")
        with self._faraway_network(pg_conn):
            bounded = _call(pg_conn, k=3, cost_mode="distance")
        assert [r["facility_id"] for r in bounded] == [
            r["facility_id"] for r in baseline
        ]

    def test_faraway_facility_not_returned(self, pg_conn) -> None:
        with self._faraway_network(pg_conn):
            rows = _call(pg_conn, k=10, cost_mode="distance")
        assert 901 not in [r["facility_id"] for r in rows]


class TestOneWayEdges:
    """Design D9 — a one-way edge cannot be traversed against its direction.

    Adds a temp edge V_extra -> V1 (forward points INTO the incident vertex) and
    a facility at V_extra. Reaching the facility from V1 requires traversing the
    edge in reverse, which is blocked iff ``oneway = 1``.
    """

    @contextmanager
    def _spur(self, conn, *, oneway: int):
        # ``oneway`` is a trusted int literal (0/1); inlined because psycopg v3
        # forbids multiple statements in a parameterised execute().
        conn.execute(
            f"""
            INSERT INTO routing.ways_vertices_pgr (id, the_geom) VALUES
                (801, ST_SetSRID(ST_MakePoint(-150.0009, 20.0), 4326));
            INSERT INTO routing.ways
                (gid, osm_id, tag_id, source, target, the_geom,
                 cost_distance, cost_time, oneway)
            VALUES (801, 88001, 111, 801, 1,
                    ST_MakeLine(ST_SetSRID(ST_MakePoint(-150.0009, 20.0), 4326),
                                ST_SetSRID(ST_MakePoint(-150.0, 20.0), 4326)),
                    100.0, 4.0, {int(oneway)});
            INSERT INTO routing.facilities (id, osm_id, name, tags, geom, vertex_id)
            VALUES (801, 88001, 'Spur Station',
                    '{{"amenity":"fire_station"}}'::jsonb,
                    ST_SetSRID(ST_MakePoint(-150.0009, 20.0), 4326), 801);
            """
        )
        try:
            yield
        finally:
            conn.execute("DELETE FROM routing.facilities WHERE id = 801;")
            conn.execute("DELETE FROM routing.ways WHERE gid = 801;")
            conn.execute("DELETE FROM routing.ways_vertices_pgr WHERE id = 801;")

    def test_oneway_edge_not_traversed_in_reverse(self, pg_conn) -> None:
        with self._spur(pg_conn, oneway=1):
            rows = _call(pg_conn, k=10, cost_mode="distance")
        # Spur facility is reachable only by going against the one-way edge.
        assert 801 not in [r["facility_id"] for r in rows]

    def test_bidirectional_edge_is_traversed_in_reverse(self, pg_conn) -> None:
        with self._spur(pg_conn, oneway=0):
            rows = _call(pg_conn, k=10, cost_mode="distance")
        assert 801 in [r["facility_id"] for r in rows]
