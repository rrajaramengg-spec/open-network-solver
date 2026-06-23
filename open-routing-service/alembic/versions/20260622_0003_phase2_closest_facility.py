"""Phase 2 — create closest_facility PL/pgSQL function + bump function_version.

Revision ID: 20260622_0003
Revises: 20260622_0002
Create Date: 2026-06-22

Per task 2.4 + ``closest-facility`` spec.

Design D3 — four-stage flow:
  1. Candidate set: facilities within buffer_m via ST_DWithin.
  2. Snap incident: find nearest vertex within 250 m; raise incident_off_graph
     on failure.
  3. Routing: pgr_dijkstra one-to-many (single start_vid, array of end_vids).
  4. Assemble: join edge geometries into a LineString per facility; rank by
     agg_cost; LIMIT k.

Function signature:
  closest_facility(
      incident        geometry(Point,4326),
      buffer_m        double precision,
      facility_filter jsonb,
      k               integer,
      cost_mode       text       -- 'distance' or 'time'
  ) RETURNS TABLE (
      facility_id       bigint,
      rank              integer,
      total_cost        double precision,
      total_distance_m  double precision,
      total_time_s      double precision,
      route_geom        geometry   -- LineString, SRID 4326; NULL when
                                    -- routed via vertex only (no edges)
  )

Return semantics:
  * Zero rows when no facility is within buffer_m (not an error).
  * Fewer than k rows when fewer than k facilities are reachable.
  * Raises SQLSTATE 'CF001' with message 'incident_off_graph' when the
    incident point is > 250 m from the nearest road vertex.

The migration also bumps function_version to 'v2' so that cached responses
built against the previous (missing) function are immediately invalidated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260622_0003"
down_revision: str | None | Sequence[str] = "20260622_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# PL/pgSQL function body
# ---------------------------------------------------------------------------

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
DECLARE
    _incident_vid   bigint;
    _snap_dist_m    double precision;
    _max_snap_m     double precision := 250.0;
    _cost_col       text;
    _edges_sql      text;
    _end_vids       bigint[];
BEGIN
    -- -----------------------------------------------------------------------
    -- Validate cost_mode
    -- -----------------------------------------------------------------------
    IF cost_mode NOT IN ('distance', 'time') THEN
        RAISE EXCEPTION 'invalid cost_mode: %; expected ''distance'' or ''time''',
            cost_mode;
    END IF;

    _cost_col := CASE cost_mode
        WHEN 'distance' THEN 'cost_distance'
        WHEN 'time'     THEN 'cost_time'
    END;

    -- -----------------------------------------------------------------------
    -- Stage 1: Snap incident to the nearest routing vertex within 250 m
    -- -----------------------------------------------------------------------
    SELECT v.id, ST_Distance(v.the_geom::geography, incident::geography)
    INTO _incident_vid, _snap_dist_m
    FROM routing.ways_vertices_pgr v
    ORDER BY v.the_geom <-> incident   -- KNN index scan (fast first-pass)
    LIMIT 1;

    IF _incident_vid IS NULL OR _snap_dist_m > _max_snap_m THEN
        RAISE EXCEPTION 'incident_off_graph'
            USING ERRCODE = 'P0001',
                  HINT = format(
                      'Nearest vertex is %.0f m away (max %.0f m). '
                      'Move the incident closer to a road.',
                      COALESCE(_snap_dist_m, -1)::int, _max_snap_m::int
                  );
    END IF;

    -- -----------------------------------------------------------------------
    -- Stage 2: Candidate facility set filtered by buffer + user filter
    -- -----------------------------------------------------------------------
    SELECT array_agg(DISTINCT f.vertex_id)
    INTO _end_vids
    FROM routing.facilities f
    WHERE f.vertex_id IS NOT NULL
      AND ST_DWithin(f.geom::geography, incident::geography, buffer_m)
      AND (
          facility_filter IS NULL
          OR facility_filter = '{}'::jsonb
          OR (
              -- Match all key/value pairs in the filter against the facility's tags.
              -- e.g. '{"amenity":"fire_station"}' matches only when tags @> filter.
              f.tags @> facility_filter
          )
      );

    -- No facilities in buffer — return empty result set (not an error).
    IF _end_vids IS NULL OR array_length(_end_vids, 1) = 0 THEN
        RETURN;
    END IF;

    -- -----------------------------------------------------------------------
    -- Stage 3: One-to-many Dijkstra (single start, array of end vids)
    -- -----------------------------------------------------------------------
    _edges_sql := format(
        'SELECT gid AS id, source, target, %I AS cost, %I AS reverse_cost '
        'FROM routing.ways',
        _cost_col, _cost_col
    );

    -- -----------------------------------------------------------------------
    -- Stage 4: Assemble results — join edges back to geometry, rank, LIMIT k
    -- -----------------------------------------------------------------------
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
            d.path_seq
        FROM dijkstra d
        JOIN routing.ways w ON w.gid = d.edge
        WHERE d.edge > 0   -- edge = -1 on the final (destination) row
    ),
    route_lines AS (
        SELECT
            facility_vid,
            total_cost,
            ST_LineMerge(ST_Collect(edge_geom ORDER BY path_seq)) AS route_geom
        FROM path_edges
        GROUP BY facility_vid, total_cost
    ),
    ranked AS (
        SELECT
            f.id                                             AS facility_id,
            ROW_NUMBER() OVER (ORDER BY rl.total_cost ASC)  AS rank,
            rl.total_cost,
            -- Always expose both costs regardless of cost_mode for the caller.
            (SELECT SUM(w2.cost_distance) FROM dijkstra d2
             JOIN routing.ways w2 ON w2.gid = d2.edge
             WHERE d2.end_vid = rl.facility_vid AND d2.edge > 0)
                                                             AS total_distance_m,
            (SELECT SUM(w2.cost_time) FROM dijkstra d2
             JOIN routing.ways w2 ON w2.gid = d2.edge
             WHERE d2.end_vid = rl.facility_vid AND d2.edge > 0)
                                                             AS total_time_s,
            rl.route_geom
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

_DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS public.closest_facility(geometry, double precision, jsonb, integer, text);"

_BUMP_VERSION_SQL = "UPDATE function_version SET version = 'v2', updated_at = now() WHERE id = 1;"
_UNBUMP_VERSION_SQL = "UPDATE function_version SET version = 'v1', updated_at = now() WHERE id = 1;"


def upgrade() -> None:
    op.execute(sa.text(_FUNCTION_SQL))
    # Bump function_version so cached responses built before this migration are
    # invalidated by the new cache key (design D9).
    op.execute(sa.text(_BUMP_VERSION_SQL))


def downgrade() -> None:
    op.execute(sa.text(_DROP_FUNCTION_SQL))
    op.execute(sa.text(_UNBUMP_VERSION_SQL))
