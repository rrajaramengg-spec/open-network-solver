"""FastAPI routers."""

from open_routing_service.api.etl_status import router as etl_status_router
from open_routing_service.api.health import router as health_router
from open_routing_service.api.metrics import router as metrics_router
from open_routing_service.api.routing import router as routing_router

__all__ = [
    "etl_status_router",
    "health_router",
    "metrics_router",
    "routing_router",
]
