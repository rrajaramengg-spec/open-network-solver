#!/usr/bin/env bash
# Thin shell entrypoint: forwards all arguments to the Python orchestrator.
# Kept separate so future ETL variants (delta loads, partial bbox loads,
# Phase-5 vector-tile rebuilds chained off the same compose service) can swap
# implementations without changing the container ENTRYPOINT contract.
set -euo pipefail
exec python3 /app/load_osm.py "$@"
