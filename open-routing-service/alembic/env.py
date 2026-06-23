"""Alembic environment.

Uses a **synchronous** engine (psycopg2) because Alembic migrations are
bounded, single-shot operations that we invoke from both sync CLI
(``alembic upgrade head``) and sync test code
(``alembic.command.upgrade(...)``). The production runtime stack continues to
use ``async_engine`` (asyncpg) for application traffic.

External schema policy (design D10):
  * pgRouting topology tables (``ways``, ``ways_vertices_pgr``) are owned by
    ``osm2pgrouting`` inside the ETL. Alembic does NOT manage them.
  * ``include_object`` below excludes anything not declared on our metadata,
    preventing accidental autogenerate diffs against the external schema.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from open_routing_service.config import get_settings
from open_routing_service.models.db_base import Base

# Import all models so they register on Base.metadata BEFORE autogenerate runs.
# Keep this list explicit; new models added in later phases must extend it.
from open_routing_service.models import etl_runs as _etl_runs  # noqa: F401
from open_routing_service.models import facilities as _facilities  # noqa: F401
from open_routing_service.models import function_version as _function_version  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

logger = logging.getLogger("alembic.env")

# Inject the database URL from pydantic-settings (alembic.ini leaves it blank).
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.routing_db_url_sync_for_alembic)

target_metadata = Base.metadata


def _include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Skip the external pgRouting topology tables on autogenerate.

    Anything not declared on our ``Base.metadata`` is left alone. This prevents
    autogenerate from proposing destructive drops of ``ways`` / ``ways_vertices_pgr``
    when those are present (after ETL) but unknown to our SQLAlchemy models.
    """
    if type_ == "table" and reflected and compare_to is None:
        return name in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (used by ``alembic upgrade --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live sync engine."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        _do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
