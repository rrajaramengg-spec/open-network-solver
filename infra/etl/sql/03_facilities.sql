-- Phase 2: snap and clean facilities in the staging schema.
--
-- Called AFTER the Python _extract_facilities() step has already bulk-INSERTed
-- raw POI rows into routing_next.facilities (vertex_id = NULL).  This script:
--   1. Creates spatial + amenity indexes (if not yet present).
--   2. Snaps each facility to the nearest ways_vertices_pgr vertex using an
--      index-assisted KNN (`the_geom <-> f.geom`), then drops rows whose nearest
--      vertex is beyond 100 m by true geography distance.
--   3. Deletes rows that could not be snapped (no vertex within 100 m).
--   4. Creates vertex_id index and FK constraint.
--
-- The :staging_schema variable is substituted by psql at runtime.

\echo 'phase-2 facilities-snap: indexing and snapping :"staging_schema".facilities'

CREATE INDEX IF NOT EXISTS ix_facilities_geom_staging
    ON :"staging_schema".facilities USING GIST (geom);

CREATE INDEX IF NOT EXISTS ix_facilities_amenity_staging
    ON :"staging_schema".facilities ((tags->>'amenity'));

\echo 'phase-2 facilities-snap: snapping to nearest vertex (100 m threshold)'

-- Snap each facility to its nearest vertex using index-assisted KNN: the
-- geometry `<->` operator with `f.geom` constant per row lets the planner use
-- the GIST index (ix_wv_geom_staging), giving O(facilities · log V) instead of
-- a full per-facility seq scan over every vertex. Casting to ::geography in the
-- ORDER BY (as the previous version did) defeats that index and does not scale
-- to country-size vertex sets.
UPDATE :"staging_schema".facilities f
SET vertex_id = (
    SELECT v.id
    FROM :"staging_schema".ways_vertices_pgr v
    ORDER BY v.the_geom <-> f.geom
    LIMIT 1
)
WHERE f.vertex_id IS NULL;

-- Apply the real 100 m snap threshold by true (geography) distance, dropping
-- facilities whose nearest vertex is too far. This second pass touches only the
-- already-snapped rows, so it stays cheap.
DELETE FROM :"staging_schema".facilities f
USING :"staging_schema".ways_vertices_pgr v
WHERE f.vertex_id = v.id
  AND NOT ST_DWithin(f.geom::geography, v.the_geom::geography, 100);

DELETE FROM :"staging_schema".facilities
WHERE vertex_id IS NULL;

\echo 'phase-2 facilities-snap: creating vertex_id index and FK'

CREATE INDEX IF NOT EXISTS ix_facilities_vertex_id_staging
    ON :"staging_schema".facilities (vertex_id);

-- psql variables (:'name') are NOT expanded inside dollar-quoted PL/pgSQL
-- blocks, so we stash the staging schema in a session-level custom GUC
-- and read it back via current_setting() inside the DO block.
SET ons.staging_schema TO :'staging_schema';

DO $$
DECLARE
    schema_name text := current_setting('ons.staging_schema');
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_facilities_vertex_id_staging'
    ) THEN
        EXECUTE format(
            'ALTER TABLE %I.facilities '
            'ADD CONSTRAINT fk_facilities_vertex_id_staging '
            'FOREIGN KEY (vertex_id) '
            'REFERENCES %I.ways_vertices_pgr(id) '
            'ON DELETE SET NULL',
            schema_name, schema_name
        );
    END IF;
END $$;

RESET ons.staging_schema;

-- -------------------------------------------------------------------------
-- Precompute the normalised category (design D2) and build the per-category
-- summary table, both in the staging schema so they swap in atomically with
-- the rest of the network. ``public.facility_category(jsonb)`` is created by
-- the Alembic migration (20260623_0003) and is available in this database.
-- -------------------------------------------------------------------------
\echo 'phase-2 facilities-snap: precomputing category + summary table'

ALTER TABLE :"staging_schema".facilities ADD COLUMN IF NOT EXISTS category text;

UPDATE :"staging_schema".facilities
SET category = public.facility_category(tags);

CREATE INDEX IF NOT EXISTS ix_facilities_category_staging
    ON :"staging_schema".facilities (category);

CREATE TABLE IF NOT EXISTS :"staging_schema".facility_categories (
    category text PRIMARY KEY,
    count    bigint NOT NULL
);

TRUNCATE :"staging_schema".facility_categories;

INSERT INTO :"staging_schema".facility_categories (category, count)
SELECT category, count(*)
FROM :"staging_schema".facilities
WHERE category IS NOT NULL
GROUP BY category;

\echo 'phase-2 facilities-snap: done'
