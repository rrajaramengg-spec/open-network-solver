"""facility-details-and-autocomplete Phase 1 — facility identity + geometry + stored category.

Revision ID: 20260623_0003
Revises: 20260623_0002
Create Date: 2026-06-23

The ``v7`` body (revision ``20260623_0002``) returned only routing scalars and
the route geometry. The UI had to infer the facility marker from the route's
last coordinate and could not show the facility name or POI type.

This revision (``v8``):

1. Adds a shared, ``IMMUTABLE`` SQL helper ``public.facility_category(tags jsonb)``
   that classifies a POI into a stable lowercase token (the OSM ``amenity`` /
   ``shop`` / ``tourism`` value, with ``emergency=ambulance_station`` handled
   first), falling back to ``'other'``. It is the **single source of truth** for
   the category, reused by ``closest_facility()`` and the
   ``/v1/facility-categories`` endpoint (design D2).
2. Adds a stored, indexed ``category`` column to ``routing.facilities`` and
   backfills it via the helper, so existing rows are classified without a
   re-ETL. New ETL loads populate it in ``03_facilities.sql`` (Phase 2).
3. Rewrites ``closest_facility()`` to additionally project ``facility_name``,
   the stored ``facility_category``, ``facility_tags``, and ``facility_geom``
   (read from the stored column — no per-row function calls on the hot path,
   design D3). The signature and all pre-existing result columns are unchanged
   (additive only).

``function_version`` is bumped ``v7 → v8`` so cached responses built against the
slimmer shape self-invalidate (the Redis cache key is keyed on
``function_version``). ``downgrade`` restores the verbatim ``v7`` body and drops
the column + helper.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260623_0003"
down_revision: str | None | Sequence[str] = "20260623_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Shared category-classification helper (single source of truth, design D2).
# ---------------------------------------------------------------------------
_HELPER_SQL = """\
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
"""

_DROP_HELPER_SQL = "DROP FUNCTION IF EXISTS public.facility_category(jsonb);"


# ---------------------------------------------------------------------------
# Stored category column + index + backfill.
# ---------------------------------------------------------------------------
_ADD_COLUMN_SQL = """\
ALTER TABLE routing.facilities ADD COLUMN IF NOT EXISTS category text;
"""

_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_facilities_category "
    "ON routing.facilities (category);"
)

_BACKFILL_SQL = (
    "UPDATE routing.facilities "
    "SET category = facility_category(tags) "
    "WHERE category IS NULL;"
)

_DROP_INDEX_SQL = "DROP INDEX IF EXISTS routing.ix_facilities_category;"
_DROP_COLUMN_SQL = "ALTER TABLE routing.facilities DROP COLUMN IF EXISTS category;"


# ---------------------------------------------------------------------------
# v8 — projects facility name / stored category / tags / geom (design D3).
# ---------------------------------------------------------------------------
_FUNCTION_SQL = """\
DROP FUNCTION IF EXISTS public.closest_facility(
    geometry, double precision, jsonb, integer, text
);
CREATE OR REPLACE FUNCTION public.closest_facility(
    incident        geometry(Point, 4326),
    buffer_m        double precision,
    facility_filter jsonb,
    k               integer,
    cost_mode       text
)
RETURNS TABLE (
    facility_id       bigint,
    rank              integer,
    total_cost        double precision,
    total_distance_m  double precision,
    total_time_s      double precision,
    route_geom        geometry,
    facility_name     text,
    facility_category text,
    facility_tags     jsonb,
    facility_geom     geometry
)
LANGUAGE plpgsql
STABLE
AS $func$
#variable_conflict use_column
DECLARE
    _incident_vid   bigint;
    _snap_dist_m    double precision;
    _max_snap_m     double precision := 250.0;
    _detour_factor  double precision := 2.0;
    _cost_col       text;
    _edges_sql      text;
    _end_vids       bigint[];
    _lat            double precision;
    _lon            double precision;
    _radius_m       double precision;
    _lat_deg        double precision;
    _lon_deg        double precision;
    _min_lon        double precision;
    _min_lat        double precision;
    _max_lon        double precision;
    _max_lat        double precision;
BEGIN
    IF cost_mode NOT IN ('distance', 'time') THEN
        RAISE EXCEPTION 'invalid cost_mode: %; expected ''distance'' or ''time''',
            cost_mode;
    END IF;

    _cost_col := CASE cost_mode
        WHEN 'distance' THEN 'cost_distance'
        WHEN 'time'     THEN 'cost_time'
    END;

    SELECT v.id, ST_Distance(v.the_geom::geography, incident::geography)
    INTO _incident_vid, _snap_dist_m
    FROM routing.ways_vertices_pgr v
    ORDER BY v.the_geom <-> incident
    LIMIT 1;

    IF _incident_vid IS NULL OR _snap_dist_m > _max_snap_m THEN
        RAISE EXCEPTION 'incident_off_graph'
            USING ERRCODE = 'P0001',
                  HINT = format(
                      'Nearest vertex is %s m away (max %s m). '
                      'Move the incident closer to a road.',
                      COALESCE(_snap_dist_m, -1)::int, _max_snap_m::int
                  );
    END IF;

    _lon      := ST_X(incident);
    _lat      := ST_Y(incident);
    _radius_m := buffer_m * _detour_factor;
    _lat_deg  := _radius_m / 111320.0;
    _lon_deg  := _radius_m / (111320.0 * GREATEST(cos(radians(_lat)), 0.01));
    _min_lon  := _lon - _lon_deg;
    _min_lat  := _lat - _lat_deg;
    _max_lon  := _lon + _lon_deg;
    _max_lat  := _lat + _lat_deg;

    SELECT array_agg(DISTINCT f.vertex_id)
    INTO _end_vids
    FROM routing.facilities f
    WHERE f.vertex_id IS NOT NULL
      AND f.geom && ST_MakeEnvelope(_min_lon, _min_lat, _max_lon, _max_lat, 4326)
      AND ST_DWithin(f.geom::geography, incident::geography, buffer_m)
      AND (
          facility_filter IS NULL
          OR facility_filter = '{}'::jsonb
          OR f.tags @> facility_filter
      );

    IF _end_vids IS NULL OR array_length(_end_vids, 1) = 0 THEN
        RETURN;
    END IF;

    _edges_sql := format(
        'SELECT gid AS id, source, target, %I AS cost, '
        'CASE WHEN oneway = 1 THEN -1 ELSE %I END AS reverse_cost '
        'FROM routing.ways '
        'WHERE the_geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)',
        _cost_col, _cost_col,
        _min_lon, _min_lat, _max_lon, _max_lat
    );

    RETURN QUERY
    WITH dijkstra AS (
        SELECT *
        FROM pgr_dijkstra(
            _edges_sql,
            _incident_vid,
            _end_vids,
            directed := true
        )
    ),
    totals AS (
        SELECT
            d.end_vid             AS facility_vid,
            MAX(d.agg_cost)::float8 AS total_cost
        FROM dijkstra d
        GROUP BY d.end_vid
    ),
    paths AS (
        SELECT
            d.end_vid                                                       AS facility_vid,
            ST_LineMerge(ST_Collect(w.the_geom ORDER BY d.path_seq))        AS route_geom,
            SUM(w.cost_distance)::float8                                     AS total_distance_m,
            SUM(w.cost_time)::float8                                         AS total_time_s
        FROM dijkstra d
        JOIN routing.ways w ON w.gid = d.edge
        WHERE d.edge > 0
        GROUP BY d.end_vid
    ),
    combined AS (
        SELECT
            t.facility_vid,
            t.total_cost,
            p.route_geom,
            COALESCE(p.total_distance_m, 0.0) AS total_distance_m,
            COALESCE(p.total_time_s,     0.0) AS total_time_s
        FROM totals t
        LEFT JOIN paths p ON p.facility_vid = t.facility_vid
    ),
    ranked AS (
        SELECT
            f.id                                                AS facility_id,
            ROW_NUMBER() OVER (ORDER BY c.total_cost ASC)::int  AS rank,
            c.total_cost                                        AS total_cost,
            c.total_distance_m                                  AS total_distance_m,
            c.total_time_s                                      AS total_time_s,
            c.route_geom                                        AS route_geom,
            f.name                                              AS facility_name,
            COALESCE(f.category, facility_category(f.tags))     AS facility_category,
            f.tags                                              AS facility_tags,
            f.geom                                              AS facility_geom
        FROM combined c
        JOIN routing.facilities f ON f.vertex_id = c.facility_vid
        WHERE f.vertex_id IS NOT NULL
    )
    SELECT
        r.facility_id::bigint,
        r.rank::integer,
        r.total_cost::double precision,
        r.total_distance_m::double precision,
        r.total_time_s::double precision,
        r.route_geom,
        r.facility_name::text,
        r.facility_category::text,
        r.facility_tags::jsonb,
        r.facility_geom
    FROM ranked r
    ORDER BY r.rank
    LIMIT k;
END;
$func$;
"""


# v7 body (no facility attribute columns) — restored verbatim on downgrade.
_FUNCTION_SQL_V7 = """\
DROP FUNCTION IF EXISTS public.closest_facility(
    geometry, double precision, jsonb, integer, text
);
CREATE OR REPLACE FUNCTION public.closest_facility(
    incident        geometry(Point, 4326),
    buffer_m        double precision,
    facility_filter jsonb,
    k               integer,
    cost_mode       text
)
RETURNS TABLE (
    facility_id      bigint,
    rank             integer,
    total_cost       double precision,
    total_distance_m double precision,
    total_time_s     double precision,
    route_geom       geometry
)
LANGUAGE plpgsql
STABLE
AS $func$
#variable_conflict use_column
DECLARE
    _incident_vid   bigint;
    _snap_dist_m    double precision;
    _max_snap_m     double precision := 250.0;
    _detour_factor  double precision := 2.0;
    _cost_col       text;
    _edges_sql      text;
    _end_vids       bigint[];
    _lat            double precision;
    _lon            double precision;
    _radius_m       double precision;
    _lat_deg        double precision;
    _lon_deg        double precision;
    _min_lon        double precision;
    _min_lat        double precision;
    _max_lon        double precision;
    _max_lat        double precision;
BEGIN
    IF cost_mode NOT IN ('distance', 'time') THEN
        RAISE EXCEPTION 'invalid cost_mode: %; expected ''distance'' or ''time''',
            cost_mode;
    END IF;

    _cost_col := CASE cost_mode
        WHEN 'distance' THEN 'cost_distance'
        WHEN 'time'     THEN 'cost_time'
    END;

    SELECT v.id, ST_Distance(v.the_geom::geography, incident::geography)
    INTO _incident_vid, _snap_dist_m
    FROM routing.ways_vertices_pgr v
    ORDER BY v.the_geom <-> incident
    LIMIT 1;

    IF _incident_vid IS NULL OR _snap_dist_m > _max_snap_m THEN
        RAISE EXCEPTION 'incident_off_graph'
            USING ERRCODE = 'P0001',
                  HINT = format(
                      'Nearest vertex is %s m away (max %s m). '
                      'Move the incident closer to a road.',
                      COALESCE(_snap_dist_m, -1)::int, _max_snap_m::int
                  );
    END IF;

    _lon      := ST_X(incident);
    _lat      := ST_Y(incident);
    _radius_m := buffer_m * _detour_factor;
    _lat_deg  := _radius_m / 111320.0;
    _lon_deg  := _radius_m / (111320.0 * GREATEST(cos(radians(_lat)), 0.01));
    _min_lon  := _lon - _lon_deg;
    _min_lat  := _lat - _lat_deg;
    _max_lon  := _lon + _lon_deg;
    _max_lat  := _lat + _lat_deg;

    SELECT array_agg(DISTINCT f.vertex_id)
    INTO _end_vids
    FROM routing.facilities f
    WHERE f.vertex_id IS NOT NULL
      AND f.geom && ST_MakeEnvelope(_min_lon, _min_lat, _max_lon, _max_lat, 4326)
      AND ST_DWithin(f.geom::geography, incident::geography, buffer_m)
      AND (
          facility_filter IS NULL
          OR facility_filter = '{}'::jsonb
          OR f.tags @> facility_filter
      );

    IF _end_vids IS NULL OR array_length(_end_vids, 1) = 0 THEN
        RETURN;
    END IF;

    _edges_sql := format(
        'SELECT gid AS id, source, target, %I AS cost, '
        'CASE WHEN oneway = 1 THEN -1 ELSE %I END AS reverse_cost '
        'FROM routing.ways '
        'WHERE the_geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)',
        _cost_col, _cost_col,
        _min_lon, _min_lat, _max_lon, _max_lat
    );

    RETURN QUERY
    WITH dijkstra AS (
        SELECT *
        FROM pgr_dijkstra(
            _edges_sql,
            _incident_vid,
            _end_vids,
            directed := true
        )
    ),
    totals AS (
        SELECT
            d.end_vid             AS facility_vid,
            MAX(d.agg_cost)::float8 AS total_cost
        FROM dijkstra d
        GROUP BY d.end_vid
    ),
    paths AS (
        SELECT
            d.end_vid                                                       AS facility_vid,
            ST_LineMerge(ST_Collect(w.the_geom ORDER BY d.path_seq))        AS route_geom,
            SUM(w.cost_distance)::float8                                     AS total_distance_m,
            SUM(w.cost_time)::float8                                         AS total_time_s
        FROM dijkstra d
        JOIN routing.ways w ON w.gid = d.edge
        WHERE d.edge > 0
        GROUP BY d.end_vid
    ),
    combined AS (
        SELECT
            t.facility_vid,
            t.total_cost,
            p.route_geom,
            COALESCE(p.total_distance_m, 0.0) AS total_distance_m,
            COALESCE(p.total_time_s,     0.0) AS total_time_s
        FROM totals t
        LEFT JOIN paths p ON p.facility_vid = t.facility_vid
    ),
    ranked AS (
        SELECT
            f.id                                                AS facility_id,
            ROW_NUMBER() OVER (ORDER BY c.total_cost ASC)::int  AS rank,
            c.total_cost                                        AS total_cost,
            c.total_distance_m                                  AS total_distance_m,
            c.total_time_s                                      AS total_time_s,
            c.route_geom                                        AS route_geom
        FROM combined c
        JOIN routing.facilities f ON f.vertex_id = c.facility_vid
        WHERE f.vertex_id IS NOT NULL
    )
    SELECT
        r.facility_id::bigint,
        r.rank::integer,
        r.total_cost::double precision,
        r.total_distance_m::double precision,
        r.total_time_s::double precision,
        r.route_geom
    FROM ranked r
    ORDER BY r.rank
    LIMIT k;
END;
$func$;
"""


_BUMP_VERSION_SQL = (
    "UPDATE function_version SET version = 'v8', updated_at = now() WHERE id = 1;"
)
_REVERT_VERSION_SQL = (
    "UPDATE function_version SET version = 'v7', updated_at = now() WHERE id = 1;"
)


def upgrade() -> None:
    # 1. Shared classification helper (must exist before backfill + function).
    op.execute(sa.text(_HELPER_SQL))
    # 2. Stored category column + index + backfill of existing rows.
    op.execute(sa.text(_ADD_COLUMN_SQL))
    op.execute(sa.text(_INDEX_SQL))
    op.execute(sa.text(_BACKFILL_SQL))
    # 3. v8 function projecting facility attributes + geometry.
    op.execute(sa.text(_FUNCTION_SQL))
    op.execute(sa.text(_BUMP_VERSION_SQL))


def downgrade() -> None:
    # Restore the v7 function (no facility attribute columns) first so nothing
    # references the column we are about to drop.
    op.execute(sa.text(_FUNCTION_SQL_V7))
    op.execute(sa.text(_DROP_INDEX_SQL))
    op.execute(sa.text(_DROP_COLUMN_SQL))
    op.execute(sa.text(_DROP_HELPER_SQL))
    op.execute(sa.text(_REVERT_VERSION_SQL))
