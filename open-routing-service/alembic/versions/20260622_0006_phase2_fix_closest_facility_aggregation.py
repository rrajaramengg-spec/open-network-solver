"""Phase 2.7 — fix closest_facility() per-facility aggregation.

Revision ID: 20260622_0006
Revises: 20260622_0005
Create Date: 2026-06-22

The previous revision still produced wrong results:

  * ``total_cost`` came back as ``0.0`` for every row.
  * ``route_geojson`` was identical across all ranks (it contained only
    one edge instead of the assembled path).

Root cause: the ``route_lines`` CTE grouped by ``(facility_vid, total_cost)``
where ``total_cost`` was the per-edge ``agg_cost`` from ``pgr_dijkstra``.
Since ``agg_cost`` changes on every edge of the path, that GROUP BY emitted
one row per edge per facility, defeating the path aggregation and the
ranking.

This migration rebuilds the function so that:

  * Total cost per facility = ``MAX(agg_cost)`` over the dijkstra rows for
    that end_vid (the final cumulative cost).
  * Route geometry per facility = ``ST_LineMerge(ST_Collect(edge_geom
    ORDER BY path_seq))`` grouped by ``end_vid`` only.
  * Total distance/time per facility = ``SUM(ways.cost_*)`` over the path
    edges of that facility, computed in the same group as the geometry to
    avoid a correlated subquery.
  * Multiple facilities sharing the same vertex still each get their own
    rank row.

Bumps ``function_version`` to ``v5`` to invalidate any cached responses.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260622_0006"
down_revision: str | None | Sequence[str] = "20260622_0005"
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

_BUMP_VERSION_SQL = "UPDATE function_version SET version = 'v5', updated_at = now() WHERE id = 1;"
_REVERT_VERSION_SQL = "UPDATE function_version SET version = 'v4', updated_at = now() WHERE id = 1;"


def upgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL))
    op.execute(sa.text(_BUMP_VERSION_SQL))


def downgrade() -> None:
    op.execute(sa.text(_REVERT_VERSION_SQL))
