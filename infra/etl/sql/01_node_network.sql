-- Phase 1 (scalable-routing-etl): build the routable pgRouting topology in the
-- staging schema from the raw highway ways loaded by osm2pgsql (routing.lua).
--
-- Replaces what osm2pgrouting used to do internally. Strategy (design D2/D7):
--   1. A junction is an OSM node referenced by >= 2 highway-way node slots
--      (shared by two ways, or twice within one way). Way endpoints are always
--      split points. This node-id approach is correct across bridges/tunnels
--      (they do not share a node), unlike geometric pgr_nodeNetwork.
--   2. Split each way's LineString at its junction/endpoint vertices into edges.
--   3. Build ways_vertices_pgr and assign source/target with pgr_extractVertices
--      (the pgRouting docs recipe).
--
-- Input  (from routing.lua):  :staging_schema.ways_import, :staging_schema.way_nodes
-- Output (this script):       :staging_schema.ways, :staging_schema.ways_vertices_pgr
--
-- One-way normalisation: ways tagged oneway=-1 (digitised against travel) are
-- reversed so every edge's geometry runs in the travel direction; `oneway` then
-- holds only 0 (bidirectional) or 1 (forward). closest_facility() turns oneway=1
-- into reverse_cost = -1.
--
-- Invoked by load_osm.py:
--   psql -v staging_schema=routing_next -v ON_ERROR_STOP=1 -f sql/01_node_network.sql

\set ON_ERROR_STOP on
\echo 'noding: building routable topology in :"staging_schema"'

-- --------------------------------------------------------------------------- 0
-- Target edge table (schema-compatible with the old osm2pgrouting output plus a
-- normalised `oneway` column).
DROP TABLE IF EXISTS :"staging_schema".ways CASCADE;
CREATE TABLE :"staging_schema".ways (
    gid               bigserial PRIMARY KEY,
    osm_id            bigint,
    tag_id            int,
    maxspeed_forward  int,
    oneway            int2 NOT NULL DEFAULT 0,
    the_geom          geometry(LineString, 4326),
    source            bigint,
    target            bigint
);

-- --------------------------------------------------------------------------- 1
-- Index node membership; count node degree.
CREATE INDEX IF NOT EXISTS ix_way_nodes_node ON :"staging_schema".way_nodes (node_id);

CREATE TEMP TABLE _junction AS
SELECT node_id
FROM :"staging_schema".way_nodes
GROUP BY node_id
HAVING count(*) >= 2;
CREATE INDEX ON _junction (node_id);

-- Per-way node count + geometry vertex count. A way is "aligned" when its
-- LineString vertex count equals its node count, i.e. osm2pgsql did not collapse
-- any consecutive-duplicate coordinates — only then does way_nodes.seq map 1:1
-- onto the geometry vertex index. Non-aligned ways (rare/malformed) are emitted
-- whole (split at endpoints only) to guarantee correct geometry.
CREATE TEMP TABLE _way_meta AS
SELECT wi.osm_id                                   AS way_id,
       wi.tag_id,
       wi.maxspeed_forward,
       wi.oneway,
       nn.nnodes,
       (ST_NPoints(wi.geom) = nn.nnodes)           AS aligned
FROM :"staging_schema".ways_import wi
JOIN (
    SELECT way_id, count(*) AS nnodes
    FROM :"staging_schema".way_nodes
    GROUP BY way_id
) nn ON nn.way_id = wi.osm_id;
CREATE INDEX ON _way_meta (way_id);

-- Dumped geometry vertices, one row per (way, seq). Reused for edge assembly.
CREATE TEMP TABLE _pts AS
SELECT wi.osm_id        AS way_id,
       (d.path)[1]      AS seq,
       d.geom           AS pt
FROM :"staging_schema".ways_import wi
CROSS JOIN LATERAL ST_DumpPoints(wi.geom) d;
CREATE INDEX ON _pts (way_id, seq);

-- --------------------------------------------------------------------------- 2
-- Split points per aligned way: junction nodes + the two endpoints.
CREATE TEMP TABLE _splits AS
SELECT wn.way_id, wn.seq
FROM :"staging_schema".way_nodes wn
JOIN _way_meta m   ON m.way_id = wn.way_id AND m.aligned
JOIN _junction j   ON j.node_id = wn.node_id
UNION
SELECT m.way_id, 1            FROM _way_meta m WHERE m.aligned
UNION
SELECT m.way_id, m.nnodes     FROM _way_meta m WHERE m.aligned;

-- Consecutive split seqs define each edge's [from_seq, to_seq] span.
CREATE TEMP TABLE _edge_ranges AS
SELECT way_id,
       seq                                                  AS from_seq,
       lead(seq) OVER (PARTITION BY way_id ORDER BY seq)    AS to_seq
FROM _splits;

-- Aligned ways: assemble one edge per span from the dumped vertices.
INSERT INTO :"staging_schema".ways (osm_id, tag_id, maxspeed_forward, oneway, the_geom)
SELECT er.way_id,
       m.tag_id,
       m.maxspeed_forward,
       CASE WHEN m.oneway = -1 THEN 1 ELSE m.oneway END                       AS oneway,
       CASE WHEN m.oneway = -1
            THEN ST_Reverse(ST_MakeLine(p.pt ORDER BY p.seq))
            ELSE ST_MakeLine(p.pt ORDER BY p.seq)
       END                                                                    AS the_geom
FROM _edge_ranges er
JOIN _way_meta m ON m.way_id = er.way_id
JOIN _pts p      ON p.way_id = er.way_id AND p.seq BETWEEN er.from_seq AND er.to_seq
WHERE er.to_seq IS NOT NULL
GROUP BY er.way_id, er.from_seq, er.to_seq, m.tag_id, m.maxspeed_forward, m.oneway
HAVING count(*) >= 2;

-- Non-aligned ways (rare): emit the whole way as a single edge.
INSERT INTO :"staging_schema".ways (osm_id, tag_id, maxspeed_forward, oneway, the_geom)
SELECT wi.osm_id,
       wi.tag_id,
       wi.maxspeed_forward,
       CASE WHEN wi.oneway = -1 THEN 1 ELSE wi.oneway END,
       CASE WHEN wi.oneway = -1 THEN ST_Reverse(wi.geom) ELSE wi.geom END
FROM :"staging_schema".ways_import wi
JOIN _way_meta m ON m.way_id = wi.osm_id
WHERE NOT m.aligned
  AND ST_NPoints(wi.geom) >= 2;

-- --------------------------------------------------------------------------- 3
-- Build vertices + assign source/target via pgr_extractVertices (docs recipe).
CREATE TEMP TABLE _vertices AS
SELECT *
FROM pgr_extractVertices(
    format('SELECT gid AS id, the_geom AS geom FROM %I.ways ORDER BY gid',
           :'staging_schema')
);

DROP TABLE IF EXISTS :"staging_schema".ways_vertices_pgr CASCADE;
CREATE TABLE :"staging_schema".ways_vertices_pgr (
    id        bigint PRIMARY KEY,
    the_geom  geometry(Point, 4326)
);
INSERT INTO :"staging_schema".ways_vertices_pgr (id, the_geom)
SELECT id, geom::geometry(Point, 4326) FROM _vertices;

-- out_edges -> the vertex is the edge's start  => source
WITH out_going AS (
    SELECT id AS vid, unnest(out_edges) AS eid FROM _vertices
)
UPDATE :"staging_schema".ways w
SET source = og.vid
FROM out_going og
WHERE w.gid = og.eid;

-- in_edges -> the vertex is the edge's end  => target
WITH in_coming AS (
    SELECT id AS vid, unnest(in_edges) AS eid FROM _vertices
)
UPDATE :"staging_schema".ways w
SET target = ic.vid
FROM in_coming ic
WHERE w.gid = ic.eid;

-- --------------------------------------------------------------------------- 4
-- Indexes the routing query and downstream hooks rely on.
CREATE INDEX ix_ways_geom_staging      ON :"staging_schema".ways USING gist (the_geom);
CREATE INDEX ix_ways_source_staging    ON :"staging_schema".ways (source);
CREATE INDEX ix_ways_target_staging    ON :"staging_schema".ways (target);
CREATE INDEX ix_wv_geom_staging        ON :"staging_schema".ways_vertices_pgr USING gist (the_geom);

-- Drop the import scaffolding so the schema swap promotes only the routable
-- tables (ways, ways_vertices_pgr) + facilities.
DROP TABLE IF EXISTS :"staging_schema".ways_import CASCADE;
DROP TABLE IF EXISTS :"staging_schema".way_nodes CASCADE;

\echo 'noding: done'
