"""``/v1/etl-status`` endpoint — exposes the latest etl_runs row safely.

Used by the UI `<RunbookBadge>` to display the last successful ETL timestamp.
This is a small, read-only endpoint that returns a stable JSON shape; if
``etl_runs`` is empty (fresh database, no ETL run yet) the endpoint returns
``{"last_success": null}`` rather than 404 so the UI doesn't need a special
case.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from open_routing_service.api.deps import get_primary_engine

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["etl"])


class EtlStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_success: str | None
    pbf_filename: str | None
    ways_count: int | None
    vertices_count: int | None
    status: Literal["ok", "no_runs", "error"]


@router.get("/etl-status", response_model=EtlStatusResponse)
async def etl_status(
    engine: AsyncEngine = Depends(get_primary_engine),
) -> EtlStatusResponse:
    try:
        async with asyncio.timeout(2.0):
            async with engine.connect() as conn:
                row = await conn.execute(
                    text(
                        "SELECT completed_at, pbf_filename, ways_count, vertices_count "
                        "FROM etl_runs ORDER BY completed_at DESC LIMIT 1"
                    )
                )
                result = row.first()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("etl_status query failed: %s", exc)
        return EtlStatusResponse(
            last_success=None,
            pbf_filename=None,
            ways_count=None,
            vertices_count=None,
            status="error",
        )

    if result is None:
        return EtlStatusResponse(
            last_success=None,
            pbf_filename=None,
            ways_count=None,
            vertices_count=None,
            status="no_runs",
        )

    return EtlStatusResponse(
        last_success=result[0].isoformat() if result[0] is not None else None,
        pbf_filename=result[1],
        ways_count=result[2],
        vertices_count=result[3],
        status="ok",
    )
