-- Minimal pgRouting network fixture for Phase 2 SQL unit tests.
--
-- Topology: a small 3×2 grid of 6 vertices connected by 7 directed edges.
-- Coordinates are placed in the Pacific Ocean (far from real roads) to
-- ensure tests are isolated from real data.
--
--   V1(0,0) ---e1--- V2(1,0) ---e2--- V3(2,0)
--     |                 |                 |
--    e3               e4(long)           e5
--     |                 |                 |
--   V4(0,1) ---e6--- V5(1,1) ---e7--- V6(2,1)
--
-- Edge costs (designed so routing scenarios are unambiguous):
--   e1: dist=100m   time=4s   (fast short road)
--   e2: dist=100m   time=30s  (slow road — e.g. narrow street)
--   e3: dist=110m   time=4s
--   e4: dist=500m   time=10s  (long fast road)
--   e5: dist=110m   time=4s
--   e6: dist=100m   time=4s
--   e7: dist=100m   time=4s
--
-- Facilities:
--   FA (fire_station) at V3(2,0)  — top-right corner
--   FB (hospital)     at V6(2,1)  — bottom-right corner
--   FC (fire_station) at V4(0,1)  — bottom-left corner
--
-- Incident (test start): V1(0,0)
--
-- Routing expectations (from V1):
--   distance-mode: FA (via e1+e2, 200m), FC (via e3, 110m)
--   time-mode:     V3 via e1+e2 costs 34s; V4 via e3 costs 4s → FC ranks first
--   filter hospital: only FB reachable via e3+e6+e7 (via vertices 1→4→5→6)
--   off-graph: incident placed > 250m from any vertex raises incident_off_graph

-- The schema and extensions must already exist (provided by testcontainers
-- pgrouting image). This script creates a private schema per-test to avoid
-- cross-test pollution.

CREATE SCHEMA IF NOT EXISTS routing;

-- Extensions required
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- -------------------------------------------------------------------------
-- Ways (edges)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routing.ways (
    gid             bigserial PRIMARY KEY,
    osm_id          bigint,
    tag_id          integer,
    source          bigint,
    target          bigint,
    the_geom        geometry(LineString, 4326),
    cost_distance   double precision NOT NULL DEFAULT 0,
    cost_time       double precision NOT NULL DEFAULT 0,
    maxspeed_forward double precision,
    oneway          smallint NOT NULL DEFAULT 0
);

-- -------------------------------------------------------------------------
-- Vertices
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routing.ways_vertices_pgr (
    id       bigserial PRIMARY KEY,
    the_geom geometry(Point, 4326) NOT NULL
);

-- Seed vertices at (lon, lat) pairs in the mid-Pacific.
-- Using lon/lat so ST_DWithin(::geography) works correctly.
INSERT INTO routing.ways_vertices_pgr (id, the_geom) VALUES
    (1, ST_SetSRID(ST_MakePoint(-150.0000, 20.0000), 4326)),  -- V1
    (2, ST_SetSRID(ST_MakePoint(-149.9991, 20.0000), 4326)),  -- V2 (~100m east)
    (3, ST_SetSRID(ST_MakePoint(-149.9982, 20.0000), 4326)),  -- V3 (~200m east)
    (4, ST_SetSRID(ST_MakePoint(-150.0000, 19.9991), 4326)),  -- V4 (~100m south)
    (5, ST_SetSRID(ST_MakePoint(-149.9991, 19.9991), 4326)),  -- V5
    (6, ST_SetSRID(ST_MakePoint(-149.9982, 19.9991), 4326))   -- V6
ON CONFLICT (id) DO NOTHING;

-- Seed edges
INSERT INTO routing.ways (gid, osm_id, tag_id, source, target, the_geom, cost_distance, cost_time) VALUES
    (1, 1001, 111, 1, 2, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=1),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=2)),
        100.0, 4.0),   -- e1: V1→V2
    (2, 1002, 111, 2, 3, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=2),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=3)),
        100.0, 30.0),  -- e2: V2→V3 (slow)
    (3, 1003, 111, 1, 4, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=1),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=4)),
        110.0, 4.0),   -- e3: V1→V4
    (4, 1004, 105, 2, 5, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=2),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=5)),
        500.0, 10.0),  -- e4: V2→V5 (long fast)
    (5, 1005, 111, 3, 6, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=3),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=6)),
        110.0, 4.0),   -- e5: V3→V6
    (6, 1006, 111, 4, 5, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=4),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=5)),
        100.0, 4.0),   -- e6: V4→V5
    (7, 1007, 111, 5, 6, ST_MakeLine(
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=5),
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=6)),
        100.0, 4.0)    -- e7: V5→V6
ON CONFLICT (gid) DO NOTHING;

-- -------------------------------------------------------------------------
-- Facilities
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routing.facilities (
    id         bigserial PRIMARY KEY,
    osm_id     bigint,
    name       text,
    tags       jsonb NOT NULL DEFAULT '{}'::jsonb,
    geom       geometry(Point, 4326) NOT NULL,
    vertex_id  bigint REFERENCES routing.ways_vertices_pgr(id) ON DELETE SET NULL,
    category   text
);

-- Shared category-classification helper (mirrors Alembic migration 20260623_0003).
CREATE OR REPLACE FUNCTION public.facility_category(tags jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $fc$
    SELECT CASE
        WHEN tags->>'emergency' = 'ambulance_station' THEN 'ambulance_station'
        WHEN nullif(trim(tags->>'amenity'), '') IS NOT NULL THEN lower(trim(tags->>'amenity'))
        WHEN nullif(trim(tags->>'shop'),    '') IS NOT NULL THEN lower(trim(tags->>'shop'))
        WHEN nullif(trim(tags->>'tourism'), '') IS NOT NULL THEN lower(trim(tags->>'tourism'))
        ELSE 'other'
    END;
$fc$;

INSERT INTO routing.facilities (id, osm_id, name, tags, geom, vertex_id) VALUES
    (1, 9001, 'Fire Station A',
        '{"amenity":"fire_station"}'::jsonb,
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=3), 3),
    (2, 9002, 'Hospital B',
        '{"amenity":"hospital"}'::jsonb,
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=6), 6),
    (3, 9003, 'Fire Station C',
        '{"amenity":"fire_station"}'::jsonb,
        (SELECT the_geom FROM routing.ways_vertices_pgr WHERE id=4), 4)
ON CONFLICT (id) DO NOTHING;

-- Precompute the stored category (mirrors ETL 03_facilities.sql + migration backfill).
UPDATE routing.facilities SET category = facility_category(tags) WHERE category IS NULL;

CREATE INDEX IF NOT EXISTS ix_facilities_category ON routing.facilities (category);

-- Precomputed category summary table (mirrors ETL + migration 20260623_0004).
CREATE TABLE IF NOT EXISTS routing.facility_categories (
    category text PRIMARY KEY,
    count    bigint NOT NULL
);

INSERT INTO routing.facility_categories (category, count)
SELECT category, count(*) FROM routing.facilities WHERE category IS NOT NULL GROUP BY category
ON CONFLICT (category) DO UPDATE SET count = EXCLUDED.count;

-- -------------------------------------------------------------------------
-- function_version (required by the service; seeds v2)
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.function_version (
    id         integer PRIMARY KEY CHECK (id = 1),
    version    varchar(64) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO public.function_version (id, version) VALUES (1, 'v2')
ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;
