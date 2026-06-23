"""facility-details-and-autocomplete Phase 2 — precomputed facility-category summary table.

Revision ID: 20260623_0004
Revises: 20260623_0003
Create Date: 2026-06-23

Computing the category histogram per request (``GROUP BY facility_category(tags)``
over the whole catalog) is a full-scan anti-pattern. Instead the counts are
**precomputed on write** into a tiny summary table that the
``/v1/facility-categories`` endpoint reads directly (design D2/D4).

This migration:

1. Creates ``routing.facility_categories(category text PRIMARY KEY,
   count bigint NOT NULL)`` in the live schema so the endpoint works on a
   day-zero (Alembic-only, no-ETL) database — empty table → empty list.
2. Seeds it from the already-backfilled ``routing.facilities.category`` column
   (added by ``20260623_0003``) so existing data has counts immediately.

New ETL loads rebuild the table in the **staging** schema and swap it in
atomically (see ``infra/etl/sql/03_facilities.sql``). This migration does not
touch ``closest_facility()``, so ``function_version`` is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260623_0004"
down_revision: str | None | Sequence[str] = "20260623_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS routing.facility_categories (
    category text PRIMARY KEY,
    count    bigint NOT NULL
);
"""

# Seed from the backfilled category column (no-op on a fresh, factless DB).
_SEED_SQL = """\
INSERT INTO routing.facility_categories (category, count)
SELECT category, count(*)
FROM routing.facilities
WHERE category IS NOT NULL
GROUP BY category
ON CONFLICT (category) DO UPDATE SET count = EXCLUDED.count;
"""

_DROP_TABLE_SQL = "DROP TABLE IF EXISTS routing.facility_categories;"


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS routing"))
    op.execute(sa.text(_CREATE_TABLE_SQL))
    op.execute(sa.text(_SEED_SQL))


def downgrade() -> None:
    op.execute(sa.text(_DROP_TABLE_SQL))
