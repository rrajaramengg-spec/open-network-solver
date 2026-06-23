"""SQLAlchemy declarative base.

A single Base for all ORM models we own. The pgRouting topology tables
(``ways``, ``ways_vertices_pgr``) are deliberately NOT modelled here —
``alembic/env.py`` filters them out via ``include_object``.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide declarative base for SQLAlchemy 2.x typed models."""

    pass
