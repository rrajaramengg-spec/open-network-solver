"""scalable-routing-etl Phase 3 — bbox-prefilter the facility selection in ``closest_facility()``.

Revision ID: 20260623_0002
Revises: 20260623_0001
Create Date: 2026-06-23

The ``v6`` body (revision ``20260623_0001``) bbox-bounded the *edges* handed to
``pgr_dijkstra`` (design D8) but still selected the destination facilities with
a bare::

    WHERE ST_DWithin(f.geom::geography, incident::geography, buffer_m)

The ``::geography`` cast makes the predicate non-sargable against the GIST index
on ``facilities.geom`` (a *geometry* index), so Postgres falls back to a
**parallel sequential scan over the entire ``facilities`` table**. On the
country-scale US-West load (~1.6 M facilities) that single sub-query costs
~2.2 s regardless of ``buffer_m`` — it dominates request latency and breaks the
"latency independent of network size" invariant the rest of D8 establishes
(measured: cold ``closest_facility`` p95 ≈ 2.2 s, all in this seq scan).

Fix (this revision, ``v7``): add a cheap, index-using bounding-box prefilter
*before* the precise geography distance check — the canonical PostGIS
"``&&`` then ``ST_DWithin``" pattern::

    WHERE f.geom && ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
      AND ST_DWithin(f.geom::geography, incident::geography, buffer_m)

The envelope reused is the **same** ``buffer_m * detour_factor`` box already
computed for the edges SQL. Because the detour radius (``2.0 * buffer_m``) is
strictly larger than ``buffer_m``, the box is a guaranteed superset of the true
geodesic ``buffer_m`` circle, so the precise ``ST_DWithin`` still decides
membership — correctness is unchanged, only the scan is now an index scan over a
local candidate set. The bbox computation is moved above the facility selection
so both the facility prefilter and the edges SQL share it.

``function_version`` is bumped to ``v7`` so cached responses built against the
slow ``v6`` body self-invalidate (the Redis cache key is keyed on
``function_version``). Signature and result columns are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260623_0002"
down_revision: str | None | Sequence[str] = "20260623_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# v7 — bbox computed first, facility selection gains a GIST `&&` prefilter.
_FUNCTION_SQL = """\
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

    -- Bounding box around the incident (design D8), computed BEFORE the
    -- facility selection so both the facility prefilter and the edges SQL share
    -- it. The radius is the search buffer times a detour factor so a real-road
    -- path may bow out around obstacles. Latitude: 1 deg ~= 111 320 m.
    -- Longitude shrinks by cos(lat).
    _lon      := ST_X(incident);
    _lat      := ST_Y(incident);
    _radius_m := buffer_m * _detour_factor;
    _lat_deg  := _radius_m / 111320.0;
    _lon_deg  := _radius_m / (111320.0 * GREATEST(cos(radians(_lat)), 0.01));
    _min_lon  := _lon - _lon_deg;
    _min_lat  := _lat - _lat_deg;
    _max_lon  := _lon + _lon_deg;
    _max_lat  := _lat + _lat_deg;

    -- Destination facilities: a GIST `&&` prefilter on the (geometry) index
    -- restricts the precise geodesic ST_DWithin to a local candidate set.
    -- The detour bbox is a strict superset of the buffer_m circle, so
    -- ST_DWithin still decides true membership.
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

    -- Edges SQL bounded to the bbox (size-independent) and one-way aware:
    -- reverse_cost = -1 means the edge is forward-only (design D9).
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
        -- Final cumulative cost per destination vertex.
        SELECT
            d.end_vid             AS facility_vid,
            MAX(d.agg_cost)::float8 AS total_cost
        FROM dijkstra d
        GROUP BY d.end_vid
    ),
    paths AS (
        -- Per-facility assembly: geometry + distance/time totals over edges.
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


# v6 body (no facility bbox prefilter) — restored verbatim on downgrade.
_FUNCTION_SQL_V6 = """\
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

    SELECT array_agg(DISTINCT f.vertex_id)
    INTO _end_vids
    FROM routing.facilities f
    WHERE f.vertex_id IS NOT NULL
      AND ST_DWithin(f.geom::geography, incident::geography, buffer_m)
      AND (
          facility_filter IS NULL
          OR facility_filter = '{}'::jsonb
          OR f.tags @> facility_filter
      );

    IF _end_vids IS NULL OR array_length(_end_vids, 1) = 0 THEN
        RETURN;
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

_BUMP_VERSION_SQL = "UPDATE function_version SET version = 'v7', updated_at = now() WHERE id = 1;"
_REVERT_VERSION_SQL = "UPDATE function_version SET version = 'v6', updated_at = now() WHERE id = 1;"


def upgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL))
    op.execute(sa.text(_BUMP_VERSION_SQL))


def downgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL_V6))
    op.execute(sa.text(_REVERT_VERSION_SQL))
