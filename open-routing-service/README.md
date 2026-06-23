# open-routing-service

FastAPI backend for closest-facility routing. Pure HTTP/REST in v1 (no MCP — that's a future change). Reads pgRouting topology from a Postgres read-replica; writes are limited to Alembic migrations against the primary.

See [`docs/phases/phase-3-service.md`](../docs/phases/phase-3-service.md) for the full Phase 3 walkthrough and [`docs/architecture.md`](../docs/architecture.md) for the consolidated architecture brief. The OpenSpec change lives in [`openspec/changes/closest-facility-routing-service/`](../openspec/changes/closest-facility-routing-service/).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/closest-facility` | Top-K nearest facilities with route geometry + cost |
| `GET`  | `/healthz`             | Liveness |
| `GET`  | `/readyz`              | Readiness — checks primary pool + replica `pgr_version()` + soft Redis ping. **Does NOT check Nominatim** (browser calls it directly). |
| `GET`  | `/metrics`             | Prometheus exposition |
| `GET`  | `/v1/etl-status`       | Latest `etl_runs` row + ETL freshness |
| `GET`  | `/docs`, `/redoc`, `/openapi.json` | Interactive API docs |

## Layout

```
open-routing-service/
├── pyproject.toml
├── src/open_routing_service/
│   ├── api/                # FastAPI routers (deps via request.app.state)
│   ├── services/           # ClosestFacilityService — Protocol-typed DI
│   ├── repositories/       # SQLAlchemy async (primary + replica), SQL lives here
│   ├── cache/              # closest_facility cache, asyncio.timeout-guarded
│   ├── models/             # Pydantic v2 (extra="forbid", frozen=True) + SQLAlchemy
│   ├── config/             # pydantic-settings
│   ├── observability/      # logging, METRICS_REGISTRY, request-id ASGI middleware
│   ├── errors.py           # RoutingError hierarchy with error_code + http_status
│   └── main.py             # create_app() factory + @asynccontextmanager lifespan
├── alembic/                # Schema migrations (etl_runs, function_version, facilities, closest_facility fn)
├── tests/{unit,integration,load}/
└── Dockerfile              # Multi-stage Python 3.12-slim, non-root uid 10001, graceful shutdown 30s
```

## Environment variables

| Name | Default | Purpose |
|------|---------|---------|
| `ROUTING_DB_HOST_PRIMARY` | `localhost` | Primary DB host (writes + migrations) |
| `ROUTING_DB_HOST_REPLICA` | `localhost` | Replica DB host (routing reads) |
| `ROUTING_DB_PORT` | `55432` | Primary port |
| `ROUTING_DB_PORT_REPLICA` | `55433` | Replica port |
| `ROUTING_DB_USER` | `routing` | DB user |
| `ROUTING_DB_PASSWORD` | _(required)_ | DB password |
| `ROUTING_DB_NAME` | `routing` | DB name |
| `REDIS_URL` | `redis://localhost:56379/0` | Cache URL |
| `CACHE_TTL_SECONDS` | `3600` | Cache TTL per entry |
| `CORS_ALLOW_ORIGINS` | `http://localhost:58081` | Comma-separated allow-list |
| `RATE_LIMIT_PER_MINUTE` | `60` | SlowAPI per-IP quota |
| `SHUTDOWN_GRACE_S` | `30` | SIGTERM drain window |
| `LOG_LEVEL` | `INFO` | Root log level |

## Dev quick start

```sh
python -m pip install -e .[dev]
alembic upgrade head        # uses ROUTING_DB_* env vars
pytest tests/unit/          # 166 tests pass at HEAD
pytest tests/integration/ -m e2e   # spins up docker via testcontainers
```

Run the service standalone (replica + Redis already up):

```sh
uvicorn open_routing_service.main:app --host 0.0.0.0 --port 8000 --reload
```

Or via compose (recommended — handles primary, replica, Redis, nominatim, ETL):

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile service up -d
```

## Load test

```sh
k6 run tests/load/closest_facility.k6.js
# Phase 3 gate: p95 < 200 ms cached, < 800 ms uncached at 100 RPS
# Phase 5 gate: p95 < 300 ms cached, < 1 s uncached at 500 RPS
```
