"""Unit tests for the request-id ASGI middleware.

Covers: header propagation (response echo), UUID fallback when the header is
absent, ContextVar propagation across the request scope.

Implements task 3.12: "request-id middleware (header propagation + UUID fallback)".
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from open_routing_service.observability.request_id import (
    RequestIdMiddleware,
    get_request_id,
    set_request_id,
)


async def _dummy_app(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
    """Echo back the current request_id in the response body."""
    assert scope["type"] == "http"
    rid = get_request_id()
    body = rid.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _run(
    middleware: RequestIdMiddleware,
    *,
    inbound_headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[bytes, bytes], bytes]:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "headers": inbound_headers or [],
        "path": "/x",
        "query_string": b"",
    }
    await middleware(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        m["body"] for m in sent if m["type"] == "http.response.body"
    )
    headers = dict(start["headers"])
    return start["status"], headers, body


class TestRequestIdMiddleware:
    @pytest.mark.asyncio
    async def test_generates_uuid_when_header_missing(self) -> None:
        mw = RequestIdMiddleware(_dummy_app)
        status, headers, body = await _run(mw)
        assert status == 200
        echoed = body.decode()
        # Body matches the X-Request-Id response header
        assert headers[b"x-request-id"].decode() == echoed
        # And it's a parseable UUID
        uuid.UUID(echoed)

    @pytest.mark.asyncio
    async def test_propagates_inbound_header(self) -> None:
        mw = RequestIdMiddleware(_dummy_app)
        rid = "abc-123-DEF"
        status, headers, body = await _run(
            mw, inbound_headers=[(b"x-request-id", rid.encode())]
        )
        assert status == 200
        assert body.decode() == rid
        assert headers[b"x-request-id"].decode() == rid

    @pytest.mark.asyncio
    async def test_context_var_reset_after_request(self) -> None:
        set_request_id("outer")
        mw = RequestIdMiddleware(_dummy_app)
        await _run(mw, inbound_headers=[(b"x-request-id", b"inner")])
        # Outer scope's request_id is restored after the request.
        assert get_request_id() == "outer"

    @pytest.mark.asyncio
    async def test_non_http_scope_passthrough(self) -> None:
        called: list[str] = []

        async def app(scope, receive, send):
            called.append(scope["type"])

        mw = RequestIdMiddleware(app)
        await mw({"type": "lifespan"}, lambda: None, lambda m: None)  # type: ignore[arg-type]
        assert called == ["lifespan"]


class TestGetSetRequestId:
    def test_default_is_empty(self) -> None:
        # Run inside a fresh coroutine context.
        async def inner() -> str:
            return get_request_id()

        assert asyncio.run(inner()) == ""

    def test_set_then_get(self) -> None:
        async def inner() -> str:
            set_request_id("xyz")
            return get_request_id()

        assert asyncio.run(inner()) == "xyz"
