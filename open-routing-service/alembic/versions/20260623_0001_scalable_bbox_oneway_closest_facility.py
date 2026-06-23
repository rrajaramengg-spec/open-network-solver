"""scalable-routing-etl Phase 2 — bbox-bounded + one-way ``closest_facility()``.

Revision ID: 20260623_0001
Revises: 20260622_0006
Create Date: 2026-06-23

Two changes make the routing query size-independent and direction-aware
(scalable-routing-etl design D8 + D9):

  1. **Bounded edges SQL (D8).** The previous ``v5`` body passed the *entire*
     ``routing.ways`` table to ``pgr_dijkstra``. At country scale that means
     Dijkstra loads millions of edges into memory on every request and latency
     grows with the network size. We now restrict the edges SQL to a bounding
     box around the incident, expanded by ``buffer_m * detour_factor`` (a
     constant ``2.0`` so a path may bow out around obstacles), with longitude
     degrees corrected by ``cos(lat)``. The graph handed to Dijkstra is now
     proportional to the search radius, not the network size.

  2. **One-way edges (D9).** ``reverse_cost`` is set to ``-1`` for edges whose
     ``oneway`` column is ``1`` (produced by the Phase 1 noding SQL). A negative
     ``reverse_cost`` tells ``pgr_dijkstra`` the edge cannot be traversed against
     its digitised direction.

Signature and result columns are unchanged. ``function_version`` is bumped to
``v6`` so any cached responses built before this migration self-invalidate
(the cache key is keyed on ``function_version``).

Tradeoff (documented): a route that would need to leave the bbox to reach a
facility within ``buffer_m`` will not be found. ``detour_factor = 2.0`` keeps
that case vanishingly rare for the 10-mile ``buffer_m`` cap.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260623_0001"
down_revision: str | None | Sequence[str] = "20260622_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

    -- Bounding box around the incident (design D8). The radius is the search
    -- buffer times a detour factor so a real-road path may bow out around
    -- obstacles. Latitude: 1 deg ~= 111 320 m. Longitude shrinks by cos(lat).
    _lon      := ST_X(incident);
    _lat      := ST_Y(incident);
    _radius_m := buffer_m * _detour_factor;
    _lat_deg  := _radius_m / 111320.0;
    _lon_deg  := _radius_m / (111320.0 * GREATEST(cos(radians(_lat)), 0.01));
    _min_lon  := _lon - _lon_deg;
    _min_lat  := _lat - _lat_deg;
    _max_lon  := _lon + _lon_deg;
    _max_lat  := _lat + _lat_deg;

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

# v5 body (no bbox bound, no one-way) — restored verbatim on downgrade.
_FUNCTION_SQL_V5 = """\
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
    _cost_col       text;
    _edges_sql      text;
    _end_vids       bigint[];
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

    _edges_sql := format(
        'SELECT gid AS id, source, target, %I AS cost, %I AS reverse_cost '
        'FROM routing.ways',
        _cost_col, _cost_col
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

_BUMP_VERSION_SQL = "UPDATE function_version SET version = 'v6', updated_at = now() WHERE id = 1;"
_REVERT_VERSION_SQL = "UPDATE function_version SET version = 'v5', updated_at = now() WHERE id = 1;"


def upgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL))
    op.execute(sa.text(_BUMP_VERSION_SQL))


def downgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL_V5))
    op.execute(sa.text(_REVERT_VERSION_SQL))
