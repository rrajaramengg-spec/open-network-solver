"""Phase 2 — create routing.facilities (POI catalog).

Revision ID: 20260622_0001
Revises: 20260620_0001
Create Date: 2026-06-22

Per task 2.1 + routing-network spec ("Facility catalog is derived from OSM
POIs and filterable by tag").

The table lives in the ``routing`` schema (the same schema that the ETL's
``ways`` / ``ways_vertices_pgr`` end up in after the atomic swap). The schema
is created here if it does not yet exist so this migration succeeds against a
fresh database with no prior ETL — per routing-network spec scenario
"pgRouting topology tables are treated as external schema".

The FK on ``vertex_id`` is added conditionally: it requires
``routing.ways_vertices_pgr`` to exist, which is only true after the first
Phase 1 ETL run. If the target is missing we leave the column unconstrained
and the next ETL re-adds the constraint as part of its own DDL (see
``infra/etl/sql/03_facilities.sql``). This keeps Alembic safe to run from
"day zero".

ON DELETE SET NULL on the FK means an ETL that drops a vertex (rare — only
on topology rebuild) leaves orphaned facilities pointing at NULL rather than
cascading deletes through user data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260622_0001"
down_revision: str | None | Sequence[str] = "20260620_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The routing schema is normally created by osm2pgrouting (via the ETL).
    # Create it explicitly here so Alembic can install facilities into the
    # right place on a fresh DB.
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS routing"))

    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS routing.facilities (
                id            bigserial PRIMARY KEY,
                osm_id        bigint,
                name          text,
                tags          jsonb NOT NULL DEFAULT '{}'::jsonb,
                geom          geometry(Point, 4326) NOT NULL,
                vertex_id     bigint,
                created_at    timestamptz NOT NULL DEFAULT now(),
                updated_at    timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    )

    # Spatial index for ST_DWithin candidate selection in closest_facility().
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_facilities_geom "
            "ON routing.facilities USING GIST (geom)"
        )
    )

    # B-tree on the amenity tag — the dominant facility_filter clause.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_facilities_amenity "
            "ON routing.facilities ((tags->>'amenity'))"
        )
    )

    # B-tree on vertex_id to make the JOIN in closest_facility() fast.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_facilities_vertex_id "
            "ON routing.facilities (vertex_id)"
        )
    )

    # FK on vertex_id — only addable when ways_vertices_pgr exists.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'routing'
                      AND c.relname = 'ways_vertices_pgr'
                ) AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'fk_facilities_vertex_id'
                ) THEN
                    EXECUTE 'ALTER TABLE routing.facilities '
                         || 'ADD CONSTRAINT fk_facilities_vertex_id '
                         || 'FOREIGN KEY (vertex_id) '
                         || 'REFERENCES routing.ways_vertices_pgr(id) '
                         || 'ON DELETE SET NULL';
                ELSE
                    RAISE NOTICE
                        'routing.ways_vertices_pgr is not present yet; '
                        'fk_facilities_vertex_id deferred to first ETL run.';
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    # Drop the FK first (no-op if it doesn't exist) so DROP TABLE is clean.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'fk_facilities_vertex_id'
                ) THEN
                    EXECUTE 'ALTER TABLE routing.facilities '
                         || 'DROP CONSTRAINT fk_facilities_vertex_id';
                END IF;
            END $$;
            """
        )
    )
    op.execute(sa.text("DROP INDEX IF EXISTS routing.ix_facilities_vertex_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS routing.ix_facilities_amenity"))
    op.execute(sa.text("DROP INDEX IF EXISTS routing.ix_facilities_geom"))
    op.execute(sa.text("DROP TABLE IF EXISTS routing.facilities"))
    # We deliberately do NOT drop the routing schema — it may hold
    # ETL-owned tables (ways, ways_vertices_pgr) that Alembic does not manage.
