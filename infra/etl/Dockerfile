# ETL container for closest-facility-routing-service.
#
# Single one-shot pipeline (scalable-routing-etl):
#   1. Verify PBF sha256 against etl_runs (skip if already loaded).
#   2. osm2pgsql --slim --drop --flat-nodes -O flex -S routing.lua
#        -> stream highway ways + way_nodes + facilities into routing_next.
#   3. 01_node_network.sql: node-degree split + pgr_extractVertices
#        -> build ways / ways_vertices_pgr topology.
#   4. Phase 2 hooks: cost columns + facilities snap.
#   5. VACUUM ANALYZE routing_next.*.
#   6. Atomic schema swap (swap_schema.sql) + Redis cf:* flush + etl_runs row.
#
# Why osm2pgsql (not osm2pgrouting): osm2pgrouting loads the whole graph into
# RAM and OOM-kills above city scale (~15 GB RSS on a 208 MB extract). osm2pgsql
# streams via libosmium with an on-disk flat-nodes store, so a 3 GB US-West
# extract loads within a 16 GB VM. See the design doc decisions D1/D6.

ARG DEBIAN_VERSION=bookworm-slim
FROM debian:${DEBIAN_VERSION}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# osm2pgsql (flex output, Lua, PROJ) is the scalable load engine — see
#   openspec/changes/scalable-routing-etl. The Debian package is built with
#   Lua + PROJ support, which the flex routing.lua style requires.
# postgresql-client gives us psql for the swap transaction and provenance write.
# python3 + pip for the orchestrator + structured logs.
# Acquire::Retries makes the build resilient to flaky/proxied mirrors that
# intermittently drop .deb fetches (observed behind corporate egress proxies).
RUN apt-get -o Acquire::Retries=25 update \
    && apt-get -o Acquire::Retries=25 install -y --no-install-recommends \
        ca-certificates \
        curl \
        gosu \
        osm2pgsql \
        postgresql-client-15 \
        python3 \
        python3-pip \
        python3-venv \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
ARG UID=1000
ARG GID=1000
RUN groupadd --gid ${GID} etl \
    && useradd --uid ${UID} --gid ${GID} --create-home --shell /bin/bash etl

# Python deps for the orchestrator. Keep this surface tiny — the orchestrator
# only emits JSON logs and dispatches subprocesses; it does not talk to the DB
# directly (psql does that). One dep: python-json-logger.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

WORKDIR /app
COPY --chown=etl:etl load_osm.py load_osm.sh swap_schema.sql routing.lua ./
COPY --chown=etl:etl sql/ ./sql/
RUN chmod +x ./load_osm.sh ./load_osm.py

USER etl

# tini handles SIGTERM cleanly so the swap transaction is never left open.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/load_osm.sh"]
