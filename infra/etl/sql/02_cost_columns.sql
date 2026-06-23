-- Phase 2: populate cost_distance and cost_time on the staging schema's ways
-- table after osm2pgrouting has loaded the topology.
--
-- The Python orchestrator invokes this with:
--   psql -v staging_schema=routing_next -v ON_ERROR_STOP=1 -f sql/02_cost_columns.sql
--
-- Speed lookup mirrors mapconfig.xml tag IDs and speed_tags.py.
-- cost_distance  = edge length in metres (ST_Length over geography)
-- cost_time      = seconds = length_m * 3.6 / speed_kmh
--   where speed_kmh = COALESCE(maxspeed_forward, highway_class_default, 30)
--
-- The :staging_schema variable is substituted by psql at runtime.

\echo 'phase-2 cost_columns: adding cost_distance / cost_time to :"staging_schema".ways'

ALTER TABLE :"staging_schema".ways
    ADD COLUMN IF NOT EXISTS cost_distance double precision NOT NULL DEFAULT 0;

ALTER TABLE :"staging_schema".ways
    ADD COLUMN IF NOT EXISTS cost_time double precision NOT NULL DEFAULT 0;

UPDATE :"staging_schema".ways
SET
    cost_distance = ST_Length(the_geom::geography),
    cost_time     = ST_Length(the_geom::geography) * 3.6 /
        GREATEST(
            COALESCE(
                NULLIF(maxspeed_forward, 0),
                CASE tag_id
                    WHEN 101 THEN 110  -- motorway
                    WHEN 102 THEN  90  -- motorway_link
                    WHEN 103 THEN 100  -- trunk
                    WHEN 104 THEN  80  -- trunk_link
                    WHEN 105 THEN  80  -- primary
                    WHEN 106 THEN  60  -- primary_link
                    WHEN 107 THEN  60  -- secondary
                    WHEN 108 THEN  50  -- secondary_link
                    WHEN 109 THEN  50  -- tertiary
                    WHEN 110 THEN  40  -- tertiary_link
                    WHEN 111 THEN  30  -- residential
                    WHEN 112 THEN  20  -- living_street
                    WHEN 113 THEN  30  -- unclassified
                    WHEN 114 THEN  20  -- service
                    ELSE             30  -- safe default
                END
            ),
            1  -- floor at 1 km/h to avoid division by zero
        );

\echo 'phase-2 cost_columns: done'
