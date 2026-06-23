"""open-routing-service — FastAPI backend for closest-facility routing.

Module scaffolding lands across Phases 1–3:
  * Phase 1: alembic baseline, ETL stays in `infra/etl/`.
  * Phase 2: `closest_facility` PL/pgSQL function migration.
  * Phase 3: FastAPI app factory, repositories, services, observability.

See ``openspec/changes/closest-facility-routing-service/tasks.md`` for the
phased task breakdown.
"""

__version__ = "0.1.0"
