"""Integration tests for the osm2pgsql-replacement noding SQL.

Exercises ``infra/etl/sql/01_node_network.sql`` against a real PostGIS +
pgRouting database (testcontainer or the dev stack via the ``live_db``
fixture). Hand-built ``ways_import`` / ``way_nodes`` fixtures validate the
core topology algorithm:

  * X-intersection (two ways sharing a mid node) -> 4 edges, 5 vertices.
  * Bridge-over-road (two ways crossing geometrically but NOT sharing a node)
    -> stay disconnected (no vertex at the crossing).
  * Roundabout / closed way -> the shared/endpoint node becomes a junction.

Also asserts the schema contract the routing layer depends on
(ways / ways_vertices_pgr columns, source/target assignment, one-way
normalisation) and a ``pgr_dijkstra`` smoke route.

Implements scalable-routing-etl tasks 1.9 (DB portion) + 1.10.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_ETL_SQL = Path(__file__).resolve().parents[2].parent / "infra" / "etl" / "sql" / "01_node_network.sql"
_SCHEMA = "routing_next"


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


def _render_noding_sql(schema: str = _SCHEMA) -> str:
    """Render the psql script to plain SQL (strip meta-commands, bind the schema)."""
    out: list[str] = []
    for line in _ETL_SQL.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("\\"):  # \set, \echo
            continue
        line = line.replace(':"staging_schema"', schema)
        line = line.replace(":'staging_schema'", f"'{schema}'")
        out.append(line)
    return "\n".join(out)


@pytest.fixture()
def conn(live_db: str):  # noqa: ANN001, ARG001 — live_db sets ROUTING_DB_* env
    import psycopg2

    c = psycopg2.connect(
        host=os.environ["ROUTING_DB_HOST"],
        port=int(os.environ["ROUTING_DB_PORT"]),
        user=os.environ["ROUTING_DB_USER"],
        password=os.environ["ROUTING_DB_PASSWORD"],
        dbname=os.environ["ROUTING_DB_NAME"],
    )
    c.autocommit = True
    try:
        yield c
    finally:
        with c.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE;")
        c.close()


def _seed_import(conn, ways: list[tuple], way_nodes: list[tuple]) -> None:
    """Create routing_next.ways_import + way_nodes and load fixtures.

    ways:      (osm_id, tag_id, oneway, wkt_linestring)
    way_nodes: (way_id, node_id, seq)
    """
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE;")
        cur.execute(f"CREATE SCHEMA {_SCHEMA};")
        cur.execute(
            f"""
            CREATE TABLE {_SCHEMA}.ways_import (
                osm_id bigint, tags jsonb, highway text, tag_id int,
                maxspeed_forward int, oneway int2,
                geom geometry(LineString, 4326)
            );
            CREATE TABLE {_SCHEMA}.way_nodes (
                way_id bigint, node_id bigint, seq int
            );
            """
        )
        cur.executemany(
            f"INSERT INTO {_SCHEMA}.ways_import "
            f"(osm_id, tag_id, oneway, geom) "
            f"VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326))",
            ways,
        )
        cur.executemany(
            f"INSERT INTO {_SCHEMA}.way_nodes (way_id, node_id, seq) VALUES (%s, %s, %s)",
            way_nodes,
        )


def _run_noding(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_render_noding_sql())


def _scalar(conn, sql: str):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# X-intersection
# --------------------------------------------------------------------------- #


class TestXIntersection:
    """Two ways sharing mid node 2 -> 4 edges, 5 vertices."""

    def _seed(self, conn) -> None:
        ways = [
            (100, 111, 0, "LINESTRING(0 0, 1 0, 2 0)"),   # A: nodes 1,2,3
            (200, 111, 0, "LINESTRING(1 -1, 1 0, 1 1)"),  # B: nodes 4,2,5
        ]
        way_nodes = [
            (100, 1, 1), (100, 2, 2), (100, 3, 3),
            (200, 4, 1), (200, 2, 2), (200, 5, 3),
        ]
        _seed_import(conn, ways, way_nodes)

    def test_splits_into_four_edges(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        assert _scalar(conn, f"SELECT count(*) FROM {_SCHEMA}.ways") == 4

    def test_has_five_vertices(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        assert _scalar(conn, f"SELECT count(*) FROM {_SCHEMA}.ways_vertices_pgr") == 5

    def test_every_edge_has_source_and_target(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        nulls = _scalar(
            conn,
            f"SELECT count(*) FROM {_SCHEMA}.ways WHERE source IS NULL OR target IS NULL",
        )
        assert nulls == 0

    def test_shared_node_is_a_routing_vertex(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        # The crossing point (1,0) must be a vertex that 4 edges meet at.
        deg = _scalar(
            conn,
            f"""
            SELECT count(*) FROM {_SCHEMA}.ways w
            JOIN {_SCHEMA}.ways_vertices_pgr v
              ON (w.source = v.id OR w.target = v.id)
            WHERE ST_DWithin(v.the_geom, ST_SetSRID(ST_MakePoint(1, 0), 4326), 1e-9)
            """,
        )
        assert deg == 4


# --------------------------------------------------------------------------- #
# Bridge over road (geometric crossing, no shared node)
# --------------------------------------------------------------------------- #


class TestBridgeNotConnected:
    def _seed(self, conn) -> None:
        ways = [
            (300, 111, 0, "LINESTRING(0 5, 2 5)"),  # C: nodes 10,11
            (400, 111, 0, "LINESTRING(1 4, 1 6)"),  # D: nodes 12,13 — crosses at (1,5)
        ]
        way_nodes = [
            (300, 10, 1), (300, 11, 2),
            (400, 12, 1), (400, 13, 2),
        ]
        _seed_import(conn, ways, way_nodes)

    def test_two_edges_only(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        assert _scalar(conn, f"SELECT count(*) FROM {_SCHEMA}.ways") == 2

    def test_no_vertex_at_crossing(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        at_crossing = _scalar(
            conn,
            f"""
            SELECT count(*) FROM {_SCHEMA}.ways_vertices_pgr v
            WHERE ST_DWithin(v.the_geom, ST_SetSRID(ST_MakePoint(1, 5), 4326), 1e-9)
            """,
        )
        assert at_crossing == 0

    def test_edges_do_not_share_a_vertex(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        shared = _scalar(
            conn,
            f"""
            WITH e AS (SELECT gid, source, target FROM {_SCHEMA}.ways)
            SELECT count(*) FROM e a JOIN e b ON a.gid < b.gid
            WHERE a.source IN (b.source, b.target)
               OR a.target IN (b.source, b.target)
            """,
        )
        assert shared == 0


# --------------------------------------------------------------------------- #
# Roundabout / closed way
# --------------------------------------------------------------------------- #


class TestRoundabout:
    """Closed way (20,21,22,20) + a connector touching at node 21."""

    def _seed(self, conn) -> None:
        ways = [
            (500, 111, 0, "LINESTRING(0 0, 1 0, 0.5 1, 0 0)"),  # E: 20,21,22,20 (closed)
            (600, 111, 0, "LINESTRING(1 0, 2 0)"),              # F: 21,30 connector
        ]
        way_nodes = [
            (500, 20, 1), (500, 21, 2), (500, 22, 3), (500, 20, 4),
            (600, 21, 1), (600, 30, 2),
        ]
        _seed_import(conn, ways, way_nodes)

    def test_closed_way_endpoint_is_a_junction(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        # Node 20 (the closed-way start/end at (0,0)) must be a vertex.
        present = _scalar(
            conn,
            f"""
            SELECT count(*) FROM {_SCHEMA}.ways_vertices_pgr v
            WHERE ST_DWithin(v.the_geom, ST_SetSRID(ST_MakePoint(0, 0), 4326), 1e-9)
            """,
        )
        assert present == 1

    def test_connector_node_splits_the_loop(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        # Node 21 at (1,0) is shared by the loop + connector -> a junction where
        # multiple edges meet.
        deg = _scalar(
            conn,
            f"""
            SELECT count(*) FROM {_SCHEMA}.ways w
            JOIN {_SCHEMA}.ways_vertices_pgr v ON (w.source = v.id OR w.target = v.id)
            WHERE ST_DWithin(v.the_geom, ST_SetSRID(ST_MakePoint(1, 0), 4326), 1e-9)
            """,
        )
        assert deg >= 3


# --------------------------------------------------------------------------- #
# Schema contract + one-way normalisation + pgr_dijkstra smoke
# --------------------------------------------------------------------------- #


class TestContractAndRouting:
    def _seed(self, conn) -> None:
        ways = [
            (100, 111, 0, "LINESTRING(0 0, 1 0, 2 0)"),
            (200, 111, 1, "LINESTRING(2 0, 2 1)"),    # one-way forward
            (300, 111, -1, "LINESTRING(3 1, 2 1)"),   # one-way reverse -> normalised
        ]
        way_nodes = [
            (100, 1, 1), (100, 2, 2), (100, 3, 3),
            (200, 3, 1), (200, 4, 2),
            (300, 5, 1), (300, 4, 2),
        ]
        _seed_import(conn, ways, way_nodes)

    def test_ways_has_contract_columns(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        cols = _scalar(
            conn,
            f"""
            SELECT array_agg(column_name::text ORDER BY column_name)
            FROM information_schema.columns
            WHERE table_schema = '{_SCHEMA}' AND table_name = 'ways'
            """,
        )
        for required in ("gid", "source", "target", "the_geom", "tag_id",
                         "maxspeed_forward", "oneway"):
            assert required in cols

    def test_oneway_normalised_to_0_or_1(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        bad = _scalar(
            conn,
            f"SELECT count(*) FROM {_SCHEMA}.ways WHERE oneway NOT IN (0, 1)",
        )
        assert bad == 0

    def test_pgr_dijkstra_smoke_route(self, conn) -> None:
        self._seed(conn)
        _run_noding(conn)
        rows = _scalar(
            conn,
            f"""
            SELECT count(*) FROM pgr_dijkstra(
                'SELECT gid AS id, source, target,
                        ST_Length(the_geom::geography) AS cost,
                        CASE WHEN oneway = 1 THEN -1
                             ELSE ST_Length(the_geom::geography) END AS reverse_cost
                 FROM {_SCHEMA}.ways',
                (SELECT id FROM {_SCHEMA}.ways_vertices_pgr
                 ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(0, 0), 4326) LIMIT 1),
                (SELECT id FROM {_SCHEMA}.ways_vertices_pgr
                 ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(2, 0), 4326) LIMIT 1)
            )
            """,
        )
        assert rows >= 1
