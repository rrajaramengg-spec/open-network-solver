"""Baseline: create etl_runs and function_version tables.

Revision ID: 20260620_0001
Revises:
Create Date: 2026-06-20

Phase 1 baseline migration (design D10).

Owned objects only — the pgRouting topology tables (``ways``,
``ways_vertices_pgr``) are external and created by the ETL (osm2pgrouting),
not by Alembic.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260620_0001"
down_revision: str | None | Sequence[str] = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- etl_runs ---------------------------------------------------------
    op.create_table(
        "etl_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pbf_filename", sa.String(length=512), nullable=False),
        sa.Column("pbf_sha256", sa.String(length=64), nullable=False),
        sa.Column("pbf_published_date", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ways_count", sa.BigInteger(), nullable=False),
        sa.Column("vertices_count", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("pbf_sha256", name="uq_etl_runs_pbf_sha256"),
    )
    op.create_index(
        "ix_etl_runs_completed_at",
        "etl_runs",
        [sa.text("completed_at DESC")],
    )

    # --- function_version (single-row table) -----------------------------
    op.create_table(
        "function_version",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_function_version_singleton"),
    )
    # Seed the initial row. Bumped by any future migration that alters the
    # closest_facility function body.
    op.execute(
        sa.text(
            "INSERT INTO function_version (id, version) VALUES (1, 'v1') "
            "ON CONFLICT (id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_table("function_version")
    op.drop_index("ix_etl_runs_completed_at", table_name="etl_runs")
    op.drop_table("etl_runs")
