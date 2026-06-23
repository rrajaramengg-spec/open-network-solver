"""``routing.facilities`` — POI catalog populated from OSM data.

Per Phase 2 task 2.1 + ``routing-network`` spec ("Facility catalog is derived
from OSM POIs and filterable by tag").

The table lives in the ``routing`` schema (not the default schema where
``etl_runs`` and ``function_version`` live) because it shares the lifecycle of
``ways`` / ``ways_vertices_pgr`` — atomic schema swap during ETL replaces
all three together.

The SQLAlchemy mapping is intentionally lightweight: the routing service does
not write to this table at request time, it only reads from it via the
``closest_facility`` PL/pgSQL function. The mapping exists so Alembic
autogenerate would catch accidental drift in future migrations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from open_routing_service.models.db_base import Base


class Facility(Base):
    """A point-of-interest pre-snapped to the nearest routing vertex.

    ``vertex_id`` is nullable:
    - Before the first ETL run ``ways_vertices_pgr`` does not exist, so the
      FK constraint is conditionally added by the migration DDL (see
      ``20260622_0001_phase2_facilities.py``).
    - An ``ON DELETE SET NULL`` FK means a topology rebuild that drops a vertex
      leaves orphaned facilities pointing at NULL rather than cascading deletes.
    - Facilities with ``vertex_id IS NULL`` are skipped by ``closest_facility``.

    The FK to ``routing.ways_vertices_pgr.id`` is NOT declared here as a
    SQLAlchemy ``ForeignKey`` because ``ways_vertices_pgr`` is an external table
    (owned by the ETL, not by Alembic). Declaring it would force SQLAlchemy to
    resolve it during metadata operations. The constraint lives solely in the
    migration DDL's ``DO $$`` block.
    """

    __tablename__ = "facilities"
    __table_args__ = (
        # GIST and expression indexes are created via raw DDL in the migration
        # (unsupported by portable SQLAlchemy Index()). Only the B-tree on
        # vertex_id is declared here so autogenerate round-trips cleanly.
        Index("ix_facilities_vertex_id_orm", "vertex_id"),
        {"schema": "routing"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    osm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OSM tag values are always strings; dict[str, str] prevents accidental
    # non-string values that would round-trip oddly through JSONB.
    tags: Mapped[dict[str, str]] = mapped_column(
        JSONB(none_as_null=True),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    # geometry(Point, 4326). Modelled as String to avoid pulling GeoAlchemy2
    # into runtime deps — the service never reads/writes geom directly; the
    # closest_facility() PL/pgSQL function handles all geometry work.
    geom: Mapped[str] = mapped_column(String, nullable=False)
    # FK to routing.ways_vertices_pgr(id) ON DELETE SET NULL — managed by
    # migration DDL only. See class docstring.
    vertex_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Normalised POI category (precomputed via facility_category(tags) at ETL
    # write-time / migration backfill). Read by closest_facility() and the
    # /v1/facility-categories endpoint — never computed per request.
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"Facility(id={self.id!r}, osm_id={self.osm_id!r}, "
            f"name={self.name!r}, amenity={self.tags.get('amenity')!r})"
        )
