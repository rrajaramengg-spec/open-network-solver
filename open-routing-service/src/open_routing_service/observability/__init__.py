"""Observability primitives: request-id, JSON logging, Prometheus metrics."""

from open_routing_service.observability.logging_config import setup_logging
from open_routing_service.observability.metrics import (
    METRICS_REGISTRY,
    cache_error_total,
    cache_hit_total,
    cache_miss_total,
    closest_facility_results_count,
    request_duration_seconds,
    request_total,
)
from open_routing_service.observability.request_id import (
    RequestIdMiddleware,
    get_request_id,
    set_request_id,
)

__all__ = [
    "METRICS_REGISTRY",
    "RequestIdMiddleware",
    "cache_error_total",
    "cache_hit_total",
    "cache_miss_total",
    "closest_facility_results_count",
    "get_request_id",
    "request_duration_seconds",
    "request_total",
    "set_request_id",
    "setup_logging",
]
