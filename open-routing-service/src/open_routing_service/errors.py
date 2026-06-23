"""Custom exceptions for the routing service.

Exception → HTTP mapping is centralised in the ``services`` layer (D5):
  * ``IncidentOffGraphError``     → 422 ``incident_off_graph``
  * ``RoutingTimeoutError``       → 504 ``routing_timeout``
  * ``RoutingDBUnavailableError`` → 503 ``routing_db_unavailable``
  * (cache errors are swallowed — logged + metric, never raised)

These live separate from FastAPI handlers so the same exceptions can be raised
by the service layer regardless of whether the caller is HTTP, a future MCP
wrapper, or a CLI smoke-test.
"""

from __future__ import annotations


class RoutingError(Exception):
    """Base class for routing-domain errors.

    ``error_code`` is the stable machine-readable identifier exposed in the
    API response envelope; ``message`` is a human-readable explanation safe
    to surface to a UI.
    """

    error_code: str = "routing_error"
    http_status: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class IncidentOffGraphError(RoutingError):
    """Incident point is too far from the nearest road vertex (> 250 m)."""

    error_code = "incident_off_graph"
    http_status = 422


class RoutingTimeoutError(RoutingError):
    """The DB query exceeded ``settings.routing_call_timeout_s``."""

    error_code = "routing_timeout"
    http_status = 504


class RoutingDBUnavailableError(RoutingError):
    """Replica is unreachable or the DB connection pool is exhausted."""

    error_code = "routing_db_unavailable"
    http_status = 503
