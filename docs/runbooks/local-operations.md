# Local operations runbook

> One-stop guide for running **open-network-solver** locally — services,
> credentials, Docker commands, URLs, endpoints, common operations.
>
> For architecture, see [`../architecture.md`](../architecture.md).

---

## 1. Prerequisites

| Tool | Min version | Check |
|------|------------:|-------|
| Docker Desktop / Engine | 24.x | `docker --version` |
| Docker Compose v2 | 2.20+ | `docker compose version` |
| Python | 3.12 | `python --version` |
| Node.js | 20 LTS | `node --version` |
| `curl`, `jq`, `psql` (optional) | any | `curl --version` |

Free RAM ≥ 8 GB recommended (Nominatim alone needs ~2 GB shm).

---

## 2. One-time setup

```sh
# 1. Clone
git clone https://github.com/rrajaramengg-spec/open-network-solver.git
cd open-network-solver

# 2. Configure secrets (gitignored)
cp infra/.env.example infra/.env
#  Edit infra/.env and set at minimum:
#    POSTGRES_PASSWORD=<a strong value>
#    NOMINATIM_PASSWORD=<a strong value>
#    GRAFANA_PASSWORD=<a strong value>

# 3. Drop a small OSM extract into the data volume (for the first ETL run)
#    Get e.g. nevada-latest.osm.pbf from https://download.geofabrik.de/
docker volume create open-network-solver_osm-data
docker run --rm -v open-network-solver_osm-data:/data \
  -v ${PWD}:/host alpine \
  cp /host/nevada-latest.osm.pbf /data/osm/nevada-latest.osm.pbf
```

---

## 3. Service inventory

| Service | Container | Image | Host port | Compose profile |
|---------|-----------|-------|----------:|-----------------|
| Postgres primary | `ons-postgres-primary` | `pgrouting/pgrouting:16-3.5-3.7.3` | **55432** | default |
| Postgres replica | `ons-postgres-replica` | `pgrouting/pgrouting:16-3.5-3.7.3` | **55433** | `replica`, `service` |
| Redis | `ons-redis` | `redis:7-alpine` | **56379** | default |
| Nominatim | `ons-nominatim` | `mediagis/nominatim:4.5` | **58080** | `nominatim`, `service` |
| Photon (geocoder) | `ons-photon` | `rtuszik/photon-docker:latest` | **52322** | `photon` |
| ETL (one-shot) | `ons-etl` | `ons-etl:dev` (built) | — | `etl` |
| Routing API | `ons-routing-service` | `ons-routing-service:dev` (built) | **58000** | `service` |
| Routing UI | `ons-routing-ui` | `ons-routing-ui:dev` (built) | **58081** | `service`, `ui` |
| Prometheus | `ons-prometheus` | `prom/prometheus:v2.55.0` | **59090** | `observability`, `service` |
| Grafana | `ons-grafana` | `grafana/grafana:11.3.0` | **53000** | `observability`, `service` |
| Pumba (chaos) | `ons-pumba-killer` | `gaiaadm/pumba:0.10.2` | — | `chaos` |

Ports follow design D1 (non-default to avoid colliding with any local
Postgres / Redis already running).

---

## 4. Database credentials & connection strings

### Postgres (primary + replica share `routing` user)

| Field | Primary | Replica |
|-------|---------|---------|
| Host (host network) | `localhost` | `localhost` |
| Host (inside compose) | `postgres-routing-primary` | `postgres-routing-replica` |
| Port (host) | `55432` | `55433` |
| Port (inside compose) | `5432` | `5432` |
| DB name | `routing` | `routing` |
| User | `routing` | `routing` |
| Password | `${POSTGRES_PASSWORD}` from `infra/.env` | same |

```sh
# Connect from the host
psql "postgres://routing:${POSTGRES_PASSWORD}@localhost:55432/routing"

# Connect from another compose service
psql "postgres://routing:${POSTGRES_PASSWORD}@postgres-routing-primary:5432/routing"

# Verify replica is in recovery
psql "postgres://routing:${POSTGRES_PASSWORD}@localhost:55433/routing" \
  -tAc "SELECT pg_is_in_recovery();"
# expected: t

# Inspect ETL history
psql "postgres://routing:${POSTGRES_PASSWORD}@localhost:55432/routing" \
  -c "SELECT id, status, started_at, completed_at, ways_count FROM etl_runs ORDER BY id DESC LIMIT 5;"
```

### Redis

| Field | Value |
|-------|-------|
| URL (host) | `redis://localhost:56379/0` |
| URL (compose) | `redis://redis:6379/0` |
| Password | none (dev) |

```sh
redis-cli -p 56379 PING                       # PONG
redis-cli -p 56379 --scan --pattern 'cf:*' | head     # closest-facility responses
redis-cli -p 56379 --scan --pattern 'cfc:*' | head    # facility-category summaries
redis-cli -p 56379 DBSIZE
redis-cli -p 56379 INFO memory | grep used_memory_human
```

> Both `cf:*` and `cfc:*` are best-effort caches keyed by `function_version`.
> The ETL atomic swap deletes both namespaces on success, so a re-ETL never
> serves stale routes or category counts.

---

## 5. Boot sequences

The stack is profile-gated so you can start exactly what you need.

### 5.1 Bare data plane (Phase 1 — primary + Redis only)

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d \
  postgres-routing-primary redis
```

### 5.2 Run the ETL once (load OSM into the graph)

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile etl run --rm etl \
  --pbf /data/osm/nevada-latest.osm.pbf
# Idempotent: re-running with the same PBF short-circuits via sha256
# recorded in etl_runs. Atomic schema swap = no half-rebuilt window.
```

### 5.3 Full service stack (API + UI + replica + Nominatim + Redis)

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile service up -d
```

### 5.4 Add observability (Prometheus + Grafana)

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile service --profile observability up -d
```

### 5.5 Everything

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile etl --profile service --profile ui --profile observability up -d
```

### 5.6 Self-host the Photon geocoder (optional)

Address autocomplete defaults to the public Photon demo
(`https://photon.komoot.io`). To run it locally instead, start the `photon`
profile and point the UI build at it:

```sh
# PHOTON_COUNTRY_CODE picks a country extract (e.g. us); empty = full planet.
PHOTON_COUNTRY_CODE=us \
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile photon up -d photon
# Then build the UI with UI_PHOTON_URL=http://localhost:52322 (see infra/.env.example).
```

The geocoder is browser-direct (design D6): if Photon is down, address
autocomplete degrades but map-click + routing keep working.

### 5.6 Tear down

```sh
# Stop containers, keep volumes
docker compose -f infra/docker-compose.yml down

# Stop + delete volumes (destroys DB and Redis state)
docker compose -f infra/docker-compose.yml down -v
```

---

## 6. URLs (host machine)

| Surface | URL | Notes |
|---------|-----|-------|
| Routing API base | http://localhost:58000 | |
| Liveness | http://localhost:58000/healthz | `{"status":"ok"}` |
| Readiness | http://localhost:58000/readyz | checks primary + replica + Redis (NOT Nominatim) |
| Prometheus metrics | http://localhost:58000/metrics | |
| ETL status | http://localhost:58000/v1/etl-status | latest `etl_runs` row |
| Swagger UI | http://localhost:58000/docs | interactive |
| ReDoc | http://localhost:58000/redoc | |
| OpenAPI JSON | http://localhost:58000/openapi.json | |
| UI | http://localhost:58081 | production (nginx) |
| UI dev server | http://localhost:5173 | `npm run dev` only |
| Nominatim | http://localhost:58080 | browser-direct (D6) |
| Nominatim status | http://localhost:58080/status.php?format=json | |
| Prometheus UI | http://localhost:59090 | |
| Grafana | http://localhost:53000 | login `admin` / `${GRAFANA_PASSWORD}` |
| Grafana routing dashboard | http://localhost:53000/d/ons-routing | |

---

## 7. API endpoints — quick reference

### `POST /v1/closest-facility`

```sh
curl -s -X POST http://localhost:58000/v1/closest-facility \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: $(uuidgen)" \
  -d '{
    "incident":      { "lat": 32.71, "lon": -117.16 },
    "buffer_m":      500,
    "k":             3,
    "cost_mode":     "distance",
    "facility_filter": { "amenity": "fire_station" }
  }' | jq
```

**Defaults:** `buffer_m=152.4` (500 ft), `k=1`, `cost_mode="distance"`,
`facility_filter={}`.
**Bounds:** `lat∈[-90,90]`, `lon∈[-180,180]`, `k∈[1,10]`,
`buffer_m∈[10, 50_000]`.

| HTTP | `error_code` | Cause |
|-----:|--------------|-------|
| 200 | — | success |
| 422 | `invalid_request` | Pydantic validation |
| 422 | `incident_off_graph` | no edge within buffer |
| 429 | `rate_limited` | SlowAPI quota |
| 503 | `routing_db_unavailable` | replica pool exhausted |
| 504 | `routing_timeout` | query exceeded 5 s |

### `GET /readyz`

```json
{
  "status": "ok",
  "primary_pg": "ok",
  "replica_pg": "ok",
  "pgr_version": "3.7.3",
  "redis": "ok"
}
```

`redis: "skipped"` is **not** a failure (cache is best-effort).
Nominatim is **never** checked (D16) — UI calls it directly.

---

## 8. Common operations

### View logs

```sh
docker compose -f infra/docker-compose.yml logs -f open-routing-service
docker compose -f infra/docker-compose.yml logs --tail=200 postgres-routing-primary
docker compose -f infra/docker-compose.yml logs -f etl   # during ETL
```

### Restart one service

```sh
docker compose -f infra/docker-compose.yml restart open-routing-service
# Honours --timeout-graceful-shutdown 30 (configurable via SHUTDOWN_GRACE_S).
```

### Exec into a container

```sh
docker exec -it ons-postgres-primary psql -U routing -d routing
docker exec -it ons-redis redis-cli
docker exec -it ons-routing-service /bin/sh
```

### Flush the routing cache

```sh
redis-cli -p 56379 --scan --pattern 'cf:*' | xargs -r redis-cli -p 56379 DEL
# The ETL does this automatically after a successful schema swap.
```

### Re-run the ETL (idempotent)

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile etl run --rm etl --pbf /data/osm/<new>.osm.pbf
```

### Bootstrap the replica from the primary (one-time)

```sh
# Inside the replica container, before first start.
# -C -S replica_slot creates a *physical replication slot* so the primary
# retains WAL until the replica has consumed it. Without a slot a long
# country-scale ETL can out-run wal_keep_size and recycle a segment the
# replica still needs ("requested WAL segment ... has already been removed"),
# permanently breaking streaming until you re-seed.
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  run --rm --entrypoint /bin/bash postgres-routing-replica -c '
    rm -rf /var/lib/postgresql/data/* &&
    PGPASSWORD=$POSTGRES_PASSWORD pg_basebackup \
      -h postgres-routing-primary -U routing \
      -D /var/lib/postgresql/data -P -R -X stream \
      -C -S replica_slot
  '
docker compose -f infra/docker-compose.yml up -d postgres-routing-replica
```

> Replication connections need a matching `pg_hba.conf` rule on the primary:
> `host replication routing 0.0.0.0/0 scram-sha-256`. It lives in the primary's
> data volume (survives restarts, *not* a volume re-create).


### Check replica lag

```sh
psql "postgres://routing:${POSTGRES_PASSWORD}@localhost:55432/routing" \
  -c "SELECT client_addr, state, sync_state,
             pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes
      FROM pg_stat_replication;"
```

---

## 9. Running tests locally (without Docker for unit tests)

### Backend

```sh
cd open-routing-service
python -m venv .venv && . .venv/Scripts/Activate.ps1    # PowerShell
pip install -e ".[dev]"

pytest tests/unit/                  # 166 fast tests
pytest tests/integration/ -m e2e    # testcontainers — needs Docker running
k6 run tests/load/closest_facility.k6.js
```

### Frontend

```sh
cd open-routing-service-ui
cp .env.example .env
npm ci
npm run typecheck
npm test                            # Vitest unit tests
npm run test:e2e                    # Playwright — needs API + UI running
npm run build                       # → dist/
```

---

## 10. Troubleshooting cheatsheet

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `/readyz` returns 503 with `primary_pg: error` | Primary not started / wrong password | `docker compose logs postgres-routing-primary`; verify `POSTGRES_PASSWORD` in `infra/.env` |
| `/readyz` returns 503 with `replica_pg: error` | Replica never bootstrapped | Run the `pg_basebackup` snippet in §8 |
| `closest_facility` returns 422 `incident_off_graph` | Incident outside the loaded OSM extract | Pick a coordinate inside your PBF region or increase `buffer_m` |
| Repeated 504 `routing_timeout` | Cold cache + huge buffer + large K | Reduce `buffer_m` or `k`; let cache warm; check replica lag |
| Port already in use | Another Postgres/Redis on 5432/6379 | Override `POSTGRES_PRIMARY_PORT` / `REDIS_PORT` in `infra/.env` |
| UI shows "service degraded" badge | Routing API or Redis down | Check `/readyz`; tail service logs |
| Nominatim autocomplete blank | Nominatim still importing PBF | Wait — first boot can take 10–30 min; check http://localhost:58080/status.php |
| `cf:*` cache full | Redis `maxmemory` hit | `allkeys-lru` evicts automatically; raise `--maxmemory` if needed |
| ETL hangs at `osm2pgrouting` | Tiny RAM or huge PBF | Use a smaller extract or raise Docker RAM limit |

---

## 11. Useful one-liners

```sh
# Quickly confirm the whole stack is responsive
curl -s localhost:58000/healthz && \
  curl -s localhost:58000/readyz | jq -r '.status' && \
  curl -s localhost:58081 -o /dev/null -w "ui:%{http_code}\n"

# Show running compose services + their health
docker compose -f infra/docker-compose.yml ps

# Watch routing latency live (Prometheus query, copy-paste into Grafana Explore)
# histogram_quantile(0.95, sum(rate(routing_request_duration_seconds_bucket[1m])) by (le, endpoint))
```

---

## 12. Where to go next

* [`../architecture.md`](../architecture.md) — C4 + sequence + ER diagrams.
* [`./etl-runbook.md`](./etl-runbook.md) — deep dive on the ETL pipeline. *(internal)*
* [`./service-runbook.md`](./service-runbook.md) — on-call playbook. *(internal)*
* [`./failover.md`](./failover.md) — Postgres HA. *(internal)*
* [`./autoscaling.md`](./autoscaling.md) — scaling guide. *(internal)*
