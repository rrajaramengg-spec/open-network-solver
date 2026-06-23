"""``function_version`` — single-row table holding the current cache-key version.

Bumped by any Alembic migration that changes the body of the ``closest_facility``
PL/pgSQL function. The service reads this value at startup and folds it into the
Redis cache key (design D9 / spec "Function-only migration invalidates cache via
function_version bump"). This guarantees that a function-body change without an
ETL rerun still invalidates cached responses.

Constrained to a single row by a CHECK on ``id = 1``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from open_routing_service.models.db_base import Base


class FunctionVersion(Base):
    __tablename__ = "function_version"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_function_version_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"FunctionVersion(version={self.version!r}, updated_at={self.updated_at!r})"
