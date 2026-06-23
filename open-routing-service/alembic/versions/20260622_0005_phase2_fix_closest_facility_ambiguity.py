"""Phase 2.6 — fix closest_facility() variable/column ambiguity.

Revision ID: 20260622_0005
Revises: 20260622_0004
Create Date: 2026-06-22

``RETURNS TABLE`` turns the output column names (``total_cost``,
``route_geom``, ``rank`` …) into PL/pgSQL OUT variables. Any unqualified
reference to those names inside the function body is therefore ambiguous,
which PG rejects with::

    column reference "total_cost" is ambiguous
    DETAIL:  It could refer to either a PL/pgSQL variable or a table column.

This migration:
  1. Adds ``#variable_conflict use_column`` so the planner prefers column
     references when names collide.
  2. Qualifies every CTE reference with its table alias.
  3. Bumps ``function_version`` to ``v4`` to invalidate cache entries built
     against the broken v3.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260622_0005"
down_revision: str | None | Sequence[str] = "20260622_0004"
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
    path_edges AS (
        SELECT
            d.end_vid                           AS facility_vid,
            d.agg_cost                          AS total_cost,
            w.the_geom                          AS edge_geom,
            d.path_seq                          AS path_seq
        FROM dijkstra d
        JOIN routing.ways w ON w.gid = d.edge
        WHERE d.edge > 0
    ),
    route_lines AS (
        SELECT
            pe.facility_vid                                                AS facility_vid,
            pe.total_cost                                                  AS total_cost,
            ST_LineMerge(ST_Collect(pe.edge_geom ORDER BY pe.path_seq))    AS route_geom
        FROM path_edges pe
        GROUP BY pe.facility_vid, pe.total_cost
    ),
    ranked AS (
        SELECT
            f.id                                                AS facility_id,
            ROW_NUMBER() OVER (ORDER BY rl.total_cost ASC)::int AS rank,
            rl.total_cost                                       AS total_cost,
            (SELECT SUM(w2.cost_distance) FROM dijkstra d2
             JOIN routing.ways w2 ON w2.gid = d2.edge
             WHERE d2.end_vid = rl.facility_vid AND d2.edge > 0) AS total_distance_m,
            (SELECT SUM(w2.cost_time) FROM dijkstra d2
             JOIN routing.ways w2 ON w2.gid = d2.edge
             WHERE d2.end_vid = rl.facility_vid AND d2.edge > 0) AS total_time_s,
            rl.route_geom                                        AS route_geom
        FROM route_lines rl
        JOIN routing.facilities f ON f.vertex_id = rl.facility_vid
        WHERE f.vertex_id IS NOT NULL
    )
    SELECT
        r.facility_id::bigint,
        r.rank::integer,
        r.total_cost::double precision,
        COALESCE(r.total_distance_m, 0.0)::double precision,
        COALESCE(r.total_time_s, 0.0)::double precision,
        r.route_geom
    FROM ranked r
    ORDER BY r.rank
    LIMIT k;
END;
$func$;
"""

_BUMP_VERSION_SQL = "UPDATE function_version SET version = 'v4', updated_at = now() WHERE id = 1;"
_REVERT_VERSION_SQL = "UPDATE function_version SET version = 'v3', updated_at = now() WHERE id = 1;"


def upgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL))
    op.execute(sa.text(_BUMP_VERSION_SQL))


def downgrade() -> None:
    op.execute(sa.text(_REVERT_VERSION_SQL))
