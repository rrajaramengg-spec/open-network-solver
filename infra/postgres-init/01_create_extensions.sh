#!/usr/bin/env bash
# infra/postgres-init/01_create_extensions.sh
# Run once by the official postgres entrypoint on first container boot.
# Creates the extensions that pgrouting/pgrouting ships as packages.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
    CREATE EXTENSION IF NOT EXISTS pgrouting;
EOSQL
