"""``etl_runs`` — provenance record of every successful OSM ingestion.

Columns are pinned by the ``routing-network`` spec scenario "ETL records
provenance in etl_runs": ``pbf_filename``, ``pbf_sha256``, ``pbf_published_date``,
``started_at``, ``completed_at``, ``ways_count``, ``vertices_count``.

The ETL only INSERTs a row at successful completion (after the atomic schema
swap). A failed ETL leaves no trace here, so ``etl_runs.head`` is the source of
truth for "what data is currently live in ``routing``".
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from open_routing_service.models.db_base import Base


class EtlRun(Base):
    __tablename__ = "etl_runs"
    __table_args__ = (
        # The sha256 is the idempotency key — re-running an ETL with the same
        # PBF SHA must short-circuit (routing-network spec scenario "Re-running
        # ETL with the same PBF is idempotent").
        UniqueConstraint("pbf_sha256", name="uq_etl_runs_pbf_sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pbf_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    pbf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pbf_published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ways_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vertices_count: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:
        return (
            f"EtlRun(id={self.id!r}, pbf_filename={self.pbf_filename!r}, "
            f"pbf_sha256={self.pbf_sha256[:8]!r}..., completed_at={self.completed_at!r})"
        )
