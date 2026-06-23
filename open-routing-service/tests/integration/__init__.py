"""Integration-test package.

Tests here boot real Docker containers via testcontainers or docker compose.
All tests are marked ``@pytest.mark.e2e``.
Run with:  ``pytest -m e2e -v``
Skip with: ``pytest -m "not e2e" -v``
"""
