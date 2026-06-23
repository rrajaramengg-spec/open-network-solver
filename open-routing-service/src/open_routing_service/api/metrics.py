"""Prometheus ``/metrics`` exposition endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from open_routing_service.observability import METRICS_REGISTRY

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    payload = generate_latest(METRICS_REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
