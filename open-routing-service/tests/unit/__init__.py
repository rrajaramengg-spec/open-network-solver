"""Unit-test package.

Each module here MUST be runnable without Docker, without a database, and
without network access. Tests that need any of those belong in
``tests/integration/`` and are marked ``@pytest.mark.e2e``.
"""
