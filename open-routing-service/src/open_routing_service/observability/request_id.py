"""Request-id propagation via ContextVar + ASGI middleware.

The middleware:
  1. Reads ``X-Request-Id`` from the inbound request, or generates a UUID4.
  2. Stores the id in a ``ContextVar`` so any logger using
     :class:`RequestIdLogFilter` picks it up automatically.
  3. Echoes the id back in the ``X-Request-Id`` response header.

Implemented as a pure ASGI middleware (not ``BaseHTTPMiddleware``) so the
``ContextVar`` propagates correctly to all downstream handlers — see
``scalability-observability`` §ContextVar-Propagation.
"""

from __future__ import annotations

import contextvars
import logging
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def get_request_id() -> str:
    """Return the current request id (empty string outside any request)."""
    return _request_id.get()


def set_request_id(rid: str) -> None:
    """Set the current request id — useful for non-HTTP entry points."""
    _request_id.set(rid)


class RequestIdLogFilter(logging.Filter):
    """Inject ``request_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class RequestIdMiddleware:
    """Pure ASGI middleware: read/generate ``X-Request-Id`` and propagate."""

    def __init__(self, app, header_name: str = "x-request-id") -> None:
        self._app = app
        self._header_bytes = header_name.lower().encode("latin-1")

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw = headers.get(self._header_bytes, b"")
        rid = raw.decode("latin-1").strip() if raw else str(uuid.uuid4())

        token = _request_id.set(rid)

        async def send_with_id(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((self._header_bytes, rid.encode("latin-1")))
                message["headers"] = hdrs
            await send(message)

        try:
            await self._app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)
