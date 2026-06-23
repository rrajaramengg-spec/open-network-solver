# infra

Docker Compose orchestration for the routing stack. Pulls **upstream images unmodified** — no custom Postgres or Nominatim Dockerfile. Only the ETL container is built from a local Dockerfile (Debian + `osm2pgsql` + Python wrapper).

## Services

| Service | Image | Purpose |
|---------|-------|---------|
| `postgres-routing-primary` | `pgrouting/pgrouting:<tag>` | Primary Postgres 16 + PostGIS 3.4 + pgRouting 3.6. |
| `postgres-routing-replica` | same image | Streaming-replication read-replica (wired in Phase 3). |
| `redis`                    | `redis:7-alpine` | Hot read-through cache for closest-facility responses. |
| `nominatim`                | `mediagis/nominatim:<tag>` | OSS geocoder, fed from the same PBF. Called directly by the browser. |
| `etl` (profile: `etl`)     | locally built (`infra/etl/Dockerfile`) | One-shot ETL: `osm2pgsql` flex → in-database noding → staging schema → atomic swap. |

See [`../docs/runbooks/image-bumps.md`](../docs/runbooks/image-bumps.md) for the digest-pinning procedure (task 1.3).

## Layout

```
infra/
├── docker-compose.yml
├── etl/
│   ├── Dockerfile          # Debian + osm2pgsql + Python wrapper
│   ├── load_osm.sh         # Shell entrypoint
│   ├── load_osm.py         # Python orchestrator: sha256 check → osm2pgsql flex → in-db noding → cost cols → facilities → VACUUM → swap
│   ├── routing.lua         # osm2pgsql flex style: ways/way_nodes/facilities + tag_id/maxspeed/oneway
│   ├── sql/01_node_network.sql  # split ways at shared OSM nodes; build ways_vertices_pgr via pgr_extractVertices
│   ├── swap_schema.sql     # Atomic schema swap transaction + cache-flush hook
│   ├── speed_tags.py       # canonical tag_id → speed table; parity reference for SQL cost columns + unit tests (not baked into the image)
│   └── mapconfig.xml       # legacy tag → routing-class reference; tag_id parity source for routing.lua + unit tests (not baked into the image)
└── nominatim/              # Nominatim runtime config (lands in Phase 3)
```

(Prometheus + Grafana + tile-server are added in Phase 5.)
