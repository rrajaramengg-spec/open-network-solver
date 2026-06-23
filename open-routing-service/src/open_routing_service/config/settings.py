"""Settings for the routing service.

Single source of truth for runtime configuration. Pydantic-settings reads from
environment variables (and ``.env`` if present), validates them with Pydantic,
and exposes them as a frozen singleton.

Per ``platform-engineering`` skill: validate at boundaries, type everything,
no mutable globals beyond the cached singleton.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Read from environment variables, then from ``.env`` if present in CWD.
    All fields are required unless given a default; misconfiguration fails at
    import time rather than at first use.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        frozen=True,
    )

    # --- Routing database (primary) ------------------------------------------
    routing_db_host: str = Field(default="localhost")
    routing_db_port: int = Field(default=55432, ge=1, le=65535)
    routing_db_user: str = Field(default="routing")
    routing_db_password: str = Field(default="changeme-in-dev-only")
    routing_db_name: str = Field(default="routing")

    # --- Routing database (read-replica; used from Phase 3 onwards) ----------
    routing_db_replica_host: str | None = Field(default=None)
    routing_db_replica_port: int = Field(default=55433, ge=1, le=65535)

    # --- Pool sizing (per-engine; tuned in Phase 3 load test) ----------------
    routing_db_pool_size: int = Field(default=5, ge=1, le=100)
    routing_db_pool_max_overflow: int = Field(default=5, ge=0, le=100)
    routing_db_pool_timeout_s: float = Field(default=10.0, ge=0.1, le=60.0)

    # --- Redis ---------------------------------------------------------------
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:56379/0"))
    redis_call_timeout_s: float = Field(default=0.5, ge=0.05, le=5.0)

    # --- Logging -------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_format: Literal["json", "text"] = Field(default="text")

    # --- Routing function ----------------------------------------------------
    routing_call_timeout_s: float = Field(default=5.0, ge=0.5, le=60.0)
    function_version: str = Field(
        default="v1",
        description=(
            "Version tag baked into the Redis cache key for closest_facility. "
            "Bump on any function-body migration so stale cached responses are "
            "invalidated without a full ETL rerun (design D9)."
        ),
    )

    # --- Cache (Phase 3) -----------------------------------------------------
    cache_ttl_s: int = Field(default=3600, ge=0, le=86400)
    cache_key_prefix: str = Field(default="cf:")

    # --- Rate limiting (Phase 3) ---------------------------------------------
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)

    # --- CORS (Phase 3) ------------------------------------------------------
    cors_allow_origins: str = Field(
        default="*",
        description="Comma-separated origin allow-list; '*' for dev only.",
    )

    # --- Graceful shutdown (Phase 5) -----------------------------------------
    shutdown_grace_s: float = Field(
        default=30.0,
        ge=0.1,
        le=600.0,
        description=(
            "Maximum time the lifespan teardown waits for in-flight requests "
            "to finish before disposing the engines."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_allow_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # --- Computed URLs -------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def routing_db_url_async(self) -> str:
        """asyncpg URL for the primary, used by the SQLAlchemy async engine."""
        return self._build_dsn("postgresql+asyncpg", self.routing_db_host, self.routing_db_port)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def routing_db_url_sync_for_alembic(self) -> str:
        """psycopg2 URL for Alembic.

        Alembic migrations are bounded, single-shot operations that we run
        from sync test code (``alembic.command.upgrade``) and from sync CLI
        invocations (``alembic upgrade head``). Using a sync driver here keeps
        env.py free of ``asyncio.run`` — which would deadlock when called from
        within a pytest-asyncio event loop — and matches the offline-mode
        ``alembic upgrade --sql`` invocation that has no event loop at all.

        The production runtime stack still uses the async URL
        (``routing_db_url_async``) for application traffic.
        """
        return self._build_dsn("postgresql+psycopg2", self.routing_db_host, self.routing_db_port)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def routing_db_replica_url_async(self) -> str:
        """asyncpg URL for the replica. Falls back to the primary in Phase 1
        where the replica is not yet provisioned."""
        host = self.routing_db_replica_host or self.routing_db_host
        port = self.routing_db_replica_port if self.routing_db_replica_host else self.routing_db_port
        return self._build_dsn("postgresql+asyncpg", host, port)

    def _build_dsn(self, scheme: str, host: str, port: int) -> str:
        # PostgresDsn validates but does not let us pick the scheme; build manually.
        return f"{scheme}://{self.routing_db_user}:{self.routing_db_password}@{host}:{port}/{self.routing_db_name}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Use ``get_settings.cache_clear()`` in tests after monkeypatching env vars.
    """
    return Settings()
