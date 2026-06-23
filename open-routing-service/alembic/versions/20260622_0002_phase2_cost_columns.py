"""Phase 2 — extend routing.ways with cost_distance and cost_time columns.

Revision ID: 20260622_0002
Revises: 20260622_0001
Create Date: 2026-06-22

Per task 2.2 + routing-network spec ("Routable edge table carries distance
and time costs").

Design D4: two cost columns parameterised at query time.

  cost_distance — edge length in metres, from ST_Length(the_geom::geography).
  cost_time     — travel time in seconds, from length / speed where speed is
                  inferred from the osm2pgrouting tag_id (highway class) with
                  maxspeed_forward overriding when the OSM maxspeed tag was set.

The migration is:
  * **Conditional** — wrapped in DO $$ … IF EXISTS … so it is safe to run
    against a fresh database (before the first ETL) where routing.ways does
    not yet exist.  The Alembic migration still applies cleanly; the columns
    are created by the ETL's own SQL hook (02_cost_columns.sql) the first time
    the ETL runs.
  * **Idempotent** — uses ADD COLUMN IF NOT EXISTS so re-running the migration
    (e.g. after a failed run) does not error.

Speed lookup table (matches mapconfig.xml tag IDs):
  tag_id 101 motorway      110 km/h
  tag_id 102 motorway_link  90 km/h
  tag_id 103 trunk         100 km/h
  tag_id 104 trunk_link     80 km/h
  tag_id 105 primary        80 km/h
  tag_id 106 primary_link   60 km/h
  tag_id 107 secondary      60 km/h
  tag_id 108 secondary_link 50 km/h
  tag_id 109 tertiary       50 km/h
  tag_id 110 tertiary_link  40 km/h
  tag_id 111 residential    30 km/h
  tag_id 112 living_street  20 km/h
  tag_id 113 unclassified   30 km/h
  tag_id 114 service        20 km/h
  default (unknown)         30 km/h

The per-tag default is 30 km/h — safe for any class not in the map.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260622_0002"
down_revision: str | None | Sequence[str] = "20260622_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------
# Shared DDL/DML blocks — referenced in upgrade() and (inverse) downgrade()
# ---------------------------------------------------------------------------

_ADD_COLUMNS_SQL = """\
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'routing' AND c.relname = 'ways'
    ) THEN
        -- ADD COLUMN IF NOT EXISTS — idempotent; safe to re-run.
        ALTER TABLE routing.ways
            ADD COLUMN IF NOT EXISTS cost_distance double precision NOT NULL DEFAULT 0;
        ALTER TABLE routing.ways
            ADD COLUMN IF NOT EXISTS cost_time double precision NOT NULL DEFAULT 0;

        -- Populate cost_distance from edge geometry length in metres.
        UPDATE routing.ways
        SET cost_distance = ST_Length(the_geom::geography)
        WHERE cost_distance = 0;

        -- Populate cost_time: length_m / (speed_kmh / 3.6)
        -- maxspeed_forward (km/h) from OSM tag takes precedence over class default.
        UPDATE routing.ways
        SET cost_time = ST_Length(the_geom::geography) * 3.6 /
            GREATEST(
                COALESCE(NULLIF(maxspeed_forward, 0),
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
            )
        WHERE cost_time = 0;

        RAISE NOTICE 'cost_distance and cost_time columns populated on routing.ways';
    ELSE
        RAISE NOTICE
            'routing.ways does not exist yet; cost columns will be added by '
            'the first ETL run (02_cost_columns.sql).';
    END IF;
END $$;
"""

_DROP_COLUMNS_SQL = """\
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'routing' AND c.relname = 'ways'
    ) THEN
        ALTER TABLE routing.ways
            DROP COLUMN IF EXISTS cost_distance,
            DROP COLUMN IF EXISTS cost_time;
        RAISE NOTICE 'cost_distance and cost_time columns dropped from routing.ways';
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(sa.text(_ADD_COLUMNS_SQL))


def downgrade() -> None:
    op.execute(sa.text(_DROP_COLUMNS_SQL))
