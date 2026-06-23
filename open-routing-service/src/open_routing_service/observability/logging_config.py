"""Structured JSON logging setup.

Per ``scalability-observability`` §structured-logging:
  * JSON formatter via ``python-json-logger`` (already in deps).
  * Lazy formatting at call sites (no f-strings — pre-formatted strings break
    log aggregators' field extraction).
  * Every record carries ``request_id`` via :class:`RequestIdLogFilter`.
  * Noisy third-party loggers (``uvicorn.access``, ``sqlalchemy.engine``,
    ``httpx``, ``asyncio``) are pinned to WARNING.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from open_routing_service.observability.request_id import RequestIdLogFilter

_NOISY_LOGGERS = (
    "uvicorn.access",
    "uvicorn.error",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "httpx",
    "httpcore",
    "asyncio",
)


def setup_logging(*, level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger.

    Args:
        level: Standard logging level name (DEBUG/INFO/WARNING/ERROR).
        fmt:   ``"json"`` for production aggregation, ``"text"`` for dev.
    """
    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt == "json":
        handler.setFormatter(
            JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s — %(message)s"
            )
        )
    handler.addFilter(RequestIdLogFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
