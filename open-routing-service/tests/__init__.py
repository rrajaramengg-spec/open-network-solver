"""Test package for open-routing-service.

Layout:
  unit/         pure-Python unit tests; no Docker, no DB, run in any CI tier
  integration/  Postgres + Redis via testcontainers (marked @pytest.mark.e2e)
  e2e/          (Phase 3+) full stack via docker compose

Shared fixtures live in the topmost ``conftest.py`` and per-tier ``conftest.py``.
"""
