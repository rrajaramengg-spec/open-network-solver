"""Static-guard unit tests for the osm2pgsql flex style and noding SQL.

These tests do NOT need a database — they assert that the routing.lua flex
style and 01_node_network.sql keep their contract with the downstream SQL
(02_cost_columns.sql, 03_facilities.sql, closest_facility()):

  * tag_id mapping in routing.lua matches speed_tags.SPEED_BY_TAG_ID and
    mapconfig.xml (the cost-column SQL keys off these integers).
  * one-way handling exists (implicit motorway/roundabout classes).
  * facilities are emitted from both nodes and areas (design D10).
  * the noding SQL builds the topology with pgr_extractVertices, computes
    junctions by node degree, normalises one-way geometry, and drops the
    import scaffolding before the swap.

Implements scalable-routing-etl task 1.9 (the DB-free portion).
"""

from __future__ import annotations

import re
from pathlib import Path

import speed_tags  # noqa: E402 — infra/etl on sys.path via tests/conftest.py

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ETL_DIR = _REPO_ROOT / "infra" / "etl"
_ROUTING_LUA = _ETL_DIR / "routing.lua"
_NODE_NETWORK_SQL = _ETL_DIR / "sql" / "01_node_network.sql"
_MAPCONFIG_XML = _ETL_DIR / "mapconfig.xml"


def _lua() -> str:
    return _ROUTING_LUA.read_text(encoding="utf-8")


def _sql() -> str:
    return _NODE_NETWORK_SQL.read_text(encoding="utf-8")


def _parse_lua_tag_ids() -> dict[str, int]:
    """Extract the TAG_ID_BY_HIGHWAY table from routing.lua."""
    block = re.search(
        r"TAG_ID_BY_HIGHWAY\s*=\s*\{(.*?)\}", _lua(), re.DOTALL
    )
    assert block, "TAG_ID_BY_HIGHWAY table not found in routing.lua"
    pairs = re.findall(r"(\w+)\s*=\s*(\d+)", block.group(1))
    return {name: int(value) for name, value in pairs}


class TestTagIdParity:
    def test_lua_tag_ids_match_speed_tags(self) -> None:
        lua_ids = _parse_lua_tag_ids()
        # Every highway class the cost SQL knows about must be in the Lua map
        # with the identical tag_id.
        for highway, speed in speed_tags.SPEED_BY_HIGHWAY.items():
            assert highway in lua_ids, f"{highway} missing from routing.lua"
        # And the tag_id integers must match speed_tags.SPEED_BY_TAG_ID keys.
        assert set(lua_ids.values()) == set(speed_tags.SPEED_BY_TAG_ID)

    def test_tag_ids_are_in_documented_range(self) -> None:
        lua_ids = _parse_lua_tag_ids()
        assert all(101 <= v <= 114 for v in lua_ids.values())

    def test_motorway_is_101(self) -> None:
        assert _parse_lua_tag_ids()["motorway"] == 101

    def test_mapconfig_tag_ids_match_lua(self) -> None:
        xml = _MAPCONFIG_XML.read_text(encoding="utf-8")
        xml_ids = {
            name: int(tid)
            for name, tid in re.findall(r'name="(\w+)"\s+id="(\d+)"', xml)
            if 101 <= int(tid) <= 114
        }
        lua_ids = _parse_lua_tag_ids()
        for name, tid in xml_ids.items():
            assert lua_ids.get(name) == tid, f"{name}: lua={lua_ids.get(name)} xml={tid}"


class TestOnewayHandling:
    def test_explicit_oneway_values(self) -> None:
        lua = _lua()
        assert "oneway_direction" in lua
        # explicit yes/-1/no all handled
        assert "'yes'" in lua and "'-1'" in lua and "'no'" in lua

    def test_implicit_oneway_classes(self) -> None:
        lua = _lua()
        # motorway + roundabout are implicitly one-way
        assert "motorway" in lua
        assert "roundabout" in lua


class TestFacilities:
    def test_node_facilities(self) -> None:
        assert "process_node" in _lua()

    def test_area_facilities_via_centroid(self) -> None:
        lua = _lua()
        # area-mapped POIs captured as polygon centroids (design D10)
        assert "is_closed" in lua
        assert "centroid" in lua


class TestNodingSql:
    def test_uses_pgr_extract_vertices(self) -> None:
        assert "pgr_extractVertices" in _sql()

    def test_junction_by_node_degree(self) -> None:
        sql = _sql().lower()
        assert "group by node_id" in sql
        assert "having count(*) >= 2" in sql

    def test_builds_contract_tables(self) -> None:
        sql = _sql()
        assert ".ways" in sql
        assert ".ways_vertices_pgr" in sql

    def test_normalises_reverse_oneway_geometry(self) -> None:
        sql = _sql()
        assert "ST_Reverse" in sql
        assert "oneway = -1" in sql

    def test_assigns_source_and_target(self) -> None:
        sql = _sql().lower()
        assert "out_edges" in sql
        assert "in_edges" in sql
        assert "set source" in sql
        assert "set target" in sql

    def test_creates_gist_and_btree_indexes(self) -> None:
        sql = _sql().lower()
        assert "using gist (the_geom)" in sql
        assert "(source)" in sql
        assert "(target)" in sql

    def test_drops_import_scaffolding(self) -> None:
        sql = _sql()
        assert "DROP TABLE IF EXISTS :\"staging_schema\".ways_import" in sql
        assert "DROP TABLE IF EXISTS :\"staging_schema\".way_nodes" in sql
