#!/usr/bin/env python3
"""OSM-to-pgRouting ETL orchestrator.

Pipeline (scalable-routing-etl; design D1/D2/D6, spec ``routing-network``):

    1. Parse args, set up structured JSON logging.
    2. Compute sha256 of the input PBF; skip if already recorded in ``etl_runs``.
    3. Assert pgRouting >= 3.3 (pgr_extractVertices).
    4. Drop + recreate the ``routing_next`` staging schema.
    5. Run ``osm2pgsql`` (flex output, ``routing.lua``) with ``--slim --drop
       --flat-nodes`` to stream highway ways + way_nodes + facilities into
       ``routing_next`` with bounded memory.
    6. Run ``sql/01_node_network.sql`` — split ways at shared OSM nodes and build
       ``ways`` / ``ways_vertices_pgr`` via ``pgr_extractVertices``.
    7. Phase 2 hooks: ``sql/02_cost_columns.sql`` + ``sql/03_facilities.sql``.
    8. VACUUM ANALYZE routing_next.*.
    9. Run ``swap_schema.sql`` — atomic ``ALTER SCHEMA`` transaction + Redis
       cf:* flush + ``INSERT INTO etl_runs`` row.

The orchestrator NEVER talks to the database directly (no asyncpg here) — all
SQL goes through ``psql`` and the CLI tools. This keeps the ETL container's
runtime surface tiny.

Run::

    docker compose --profile etl run --rm etl \
        --pbf /data/osm/us-west-260619.osm.pbf

CLI flags::

    --pbf PATH    required, absolute path inside container
    --force       bypass the sha256 idempotency check

Environment::

    ROUTING_FLAT_NODES   osm2pgsql flat-nodes file path (default
                         /data/osm/flat-nodes.bin; deleted by --drop on success)
    OSM2PGSQL_CACHE_MB   osm2pgsql node-cache size in MB (default 2048)

Exit codes::

    0  success (incl. 'already loaded')
    2  user / config error (bad PBF path, missing env vars, pgRouting too old)
    4  ETL build failure (osm2pgsql / noding / SQL hooks)
    5  swap failure (atomic ALTER SCHEMA refused) — live schema untouched
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import subprocess  # noqa: S404 — orchestrator dispatches CLI tools by design
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter

LOG = logging.getLogger("etl")
LIVE_SCHEMA = "routing"
STAGING_SCHEMA = "routing_next"
PREV_SCHEMA = "routing_prev"
OSM2PGSQL_STYLE = "/app/routing.lua"
NODE_NETWORK_SQL = "/app/sql/01_node_network.sql"
SWAP_SQL = "/app/swap_schema.sql"
PHASE2_COST_SQL = "/app/sql/02_cost_columns.sql"
PHASE2_FACILITIES_SQL = "/app/sql/03_facilities.sql"


# --------------------------------------------------------------------------- #
# Logging setup
# --------------------------------------------------------------------------- #


def _configure_logging() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("LOG_FORMAT", "json").lower()
    handler = logging.StreamHandler(stream=sys.stderr)
    if log_format == "json":
        handler.setFormatter(
            JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
        )
    logging.basicConfig(level=log_level, handlers=[handler], force=True)


# --------------------------------------------------------------------------- #
# DB connectivity (env-driven; never logs the password)
# --------------------------------------------------------------------------- #


def _db_env() -> dict[str, str]:
    """Read DB connection params from environment.

    Returns a dict suitable for passing as ``env=`` to subprocess.run, so
    psql / osm2pgsql both pick up ``PG*`` vars without us putting the
    password on the command line.
    """
    required = {
        "ROUTING_DB_HOST": "PGHOST",
        "ROUTING_DB_PORT": "PGPORT",
        "ROUTING_DB_USER": "PGUSER",
        "ROUTING_DB_PASSWORD": "PGPASSWORD",
        "ROUTING_DB_NAME": "PGDATABASE",
    }
    env = os.environ.copy()
    for src, dst in required.items():
        value = os.environ.get(src)
        if not value:
            LOG.error("missing required env var", extra={"var": src})
            sys.exit(2)
        env[dst] = value
    return env


def _psql(env: dict[str, str], sql: str, *, capture: bool = False) -> str:
    """Run an inline SQL statement via psql.

    Uses ``-v ON_ERROR_STOP=1`` so the first error aborts the script.
    """
    cmd = [
        "psql",
        "-X",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd,
        env=env,
        check=True,
        capture_output=capture,
        text=True,
    )
    return result.stdout.strip() if capture else ""


def _psql_file(env: dict[str, str], path: str, *, variables: dict[str, str] | None = None) -> None:
    cmd = ["psql", "-X", "-v", "ON_ERROR_STOP=1"]
    for key, value in (variables or {}).items():
        cmd.extend(["-v", f"{key}={value}"])
    cmd.extend(["-f", path])
    subprocess.run(cmd, env=env, check=True)  # noqa: S603


# --------------------------------------------------------------------------- #
# Pipeline stages
# --------------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_already_loaded(env: dict[str, str], sha: str) -> bool:
    out = _psql(
        env,
        f"SELECT 1 FROM etl_runs WHERE pbf_sha256 = '{sha}' LIMIT 1;",
        capture=True,
    )
    return out == "1"


def _write_pgpass(env: dict[str, str]) -> None:
    """Write ``~/.pgpass`` so ``osm2pgsql`` (and all libpq tools) can authenticate.

    ``osm2pgsql`` reads the password via libpq, which honours ``PGPASSWORD`` and
    ``~/.pgpass``. We write ``.pgpass`` (chmod 0600) as the most portable path —
    a world-readable pgpass is silently ignored by all modern libpq versions.
    """
    pgpass_path = Path.home() / ".pgpass"
    line = (
        f"{env['PGHOST']}:{env['PGPORT']}:{env['PGDATABASE']}"
        f":{env['PGUSER']}:{env['PGPASSWORD']}\n"
    )
    pgpass_path.write_text(line, encoding="utf-8")
    pgpass_path.chmod(0o600)
    LOG.info("wrote .pgpass", extra={"path": str(pgpass_path), "host": env["PGHOST"]})


def _drop_and_recreate_staging(env: dict[str, str]) -> None:
    LOG.info("recreating staging schema", extra={"schema": STAGING_SCHEMA})
    _psql(env, f"DROP SCHEMA IF EXISTS {STAGING_SCHEMA} CASCADE;")
    _psql(env, f"CREATE SCHEMA {STAGING_SCHEMA};")
    # PostGIS + pgRouting extensions live in the public schema; osm2pgsql and the
    # noding SQL reach them via the default 'public, $user' search_path.


def _assert_pgr_version(env: dict[str, str], minimum: tuple[int, int] = (3, 3)) -> None:
    """Abort unless pgRouting >= ``minimum`` (pgr_extractVertices needs 3.3+)."""
    # pgRouting 3.x: pgr_version() returns a scalar text like '3.7.3'
    # (the 2.x table form `SELECT version FROM pgr_version()` no longer exists).
    raw = _psql(env, "SELECT pgr_version();", capture=True)
    parts = raw.split(".") if raw else []
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        LOG.error("could not parse pgr_version", extra={"raw": raw})
        sys.exit(2)
    if (major, minor) < minimum:
        LOG.error(
            "pgRouting too old for pgr_extractVertices",
            extra={"found": raw, "required": f">={minimum[0]}.{minimum[1]}"},
        )
        sys.exit(2)
    LOG.info("pgr_version ok", extra={"version": raw})


def _osm2pgsql(env: dict[str, str], pbf: Path) -> None:
    """Stream the PBF into the staging schema via osm2pgsql flex (routing.lua).

    Bounded memory (design D1/D6): ``--slim --drop`` keeps temporary data in
    Postgres, ``--flat-nodes`` holds node locations in an on-disk file (deleted
    automatically by ``--drop`` after import), and ``--cache`` caps node-cache
    RAM. ``--middle-schema public`` keeps osm2pgsql's bookkeeping out of the
    staging schema so the swap promotes only the routable tables.
    """
    flat_nodes = os.environ.get("ROUTING_FLAT_NODES", "/data/osm/flat-nodes.bin")
    cache_mb = os.environ.get("OSM2PGSQL_CACHE_MB", "2048")
    # The flat-nodes file holds node locations on disk so node-cache RAM stays
    # bounded at country scale (design D1). For small/medium extracts the in-RAM
    # cache is faster and avoids the heavy sparse-file mmap I/O — set
    # ROUTING_FLAT_NODES to an empty string / "none" / "off" to disable it.
    use_flat_nodes = flat_nodes.strip().lower() not in ("", "none", "off", "0")
    LOG.info(
        "osm2pgsql: streaming PBF into staging schema",
        extra={"pbf": str(pbf), "schema": STAGING_SCHEMA,
               "flat_nodes": flat_nodes if use_flat_nodes else "(disabled — in-RAM cache)",
               "cache_mb": cache_mb},
    )
    cmd = [
        "osm2pgsql",
        "--create",
        "--slim",
        "--drop",
        "--output", "flex",
        "--style", OSM2PGSQL_STYLE,
    ]
    if use_flat_nodes:
        cmd += ["--flat-nodes", flat_nodes]
    cmd += [
        "--cache", cache_mb,
        "--middle-schema", "public",
        "--log-progress", "false",
        "-d", env["PGDATABASE"],
        "-U", env["PGUSER"],
        "-H", env["PGHOST"],
        "-P", env["PGPORT"],
        str(pbf),
    ]
    try:
        subprocess.run(cmd, env=env, check=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        LOG.error("osm2pgsql failed", extra={"exit_code": exc.returncode})
        sys.exit(4)


def _node_network(env: dict[str, str]) -> None:
    """Build the routable topology from the raw ways (01_node_network.sql)."""
    LOG.info("noding: building topology", extra={"schema": STAGING_SCHEMA})
    try:
        _psql_file(env, NODE_NETWORK_SQL, variables={"staging_schema": STAGING_SCHEMA})
    except subprocess.CalledProcessError as exc:
        LOG.error("noding failed", extra={"exit_code": exc.returncode})
        sys.exit(4)


def _apply_phase2_hooks(env: dict[str, str]) -> None:
    """Apply the cost-column + facility-snap SQL hooks.

    Pipeline (facilities are already populated by osm2pgsql / routing.lua):
    1. ``02_cost_columns.sql``  — add + populate cost_distance / cost_time.
    2. ``03_facilities.sql``    — snap each facility to its nearest vertex,
                                  remove unsnapped rows, index + FK.
    """
    # Step 1: cost columns
    if os.path.exists(PHASE2_COST_SQL):
        LOG.info("applying phase-2 hook", extra={"hook": "cost-columns", "path": PHASE2_COST_SQL})
        try:
            _psql_file(env, PHASE2_COST_SQL, variables={"staging_schema": STAGING_SCHEMA})
        except subprocess.CalledProcessError as exc:
            LOG.error("phase-2 hook failed", extra={"hook": "cost-columns", "exit_code": exc.returncode})
            sys.exit(4)
    else:
        LOG.warning("phase-2 hook missing; skipping", extra={"hook": "cost-columns", "path": PHASE2_COST_SQL})

    # Step 2: snap facilities (table already filled by osm2pgsql)
    if os.path.exists(PHASE2_FACILITIES_SQL):
        LOG.info("applying phase-2 hook", extra={"hook": "facilities-snap", "path": PHASE2_FACILITIES_SQL})
        try:
            _psql_file(env, PHASE2_FACILITIES_SQL, variables={"staging_schema": STAGING_SCHEMA})
        except subprocess.CalledProcessError as exc:
            LOG.error("phase-2 hook failed", extra={"hook": "facilities-snap", "exit_code": exc.returncode})
            sys.exit(4)
    else:
        LOG.warning("phase-2 hook missing; skipping", extra={"hook": "facilities-snap", "path": PHASE2_FACILITIES_SQL})


def _vacuum_analyze_staging(env: dict[str, str]) -> None:
    LOG.info("VACUUM ANALYZE staging schema", extra={"schema": STAGING_SCHEMA})
    # VACUUM can't run inside a transaction block; psql with -c works fine.
    # PARALLEL 0 forces single-process VACUUM: parallel maintenance workers each
    # allocate a per-index dynamic-shared-memory segment, and on a country-scale
    # table (15M+ ways x 4 indexes) those segments exhaust the container's
    # default 64 MB /dev/shm ("could not resize shared memory segment ... No
    # space left on device"). Single-process keeps memory bounded (D6) and the
    # post-bulk-load VACUUM is I/O- not CPU-bound anyway.
    _psql(env, f"VACUUM (ANALYZE, PARALLEL 0) {STAGING_SCHEMA}.ways;")
    _psql(env, f"VACUUM (ANALYZE, PARALLEL 0) {STAGING_SCHEMA}.ways_vertices_pgr;")
    # Facilities table only exists after the Phase 2 hook runs; skip gracefully.
    try:
        _psql(env, f"VACUUM (ANALYZE, PARALLEL 0) {STAGING_SCHEMA}.facilities;")
    except subprocess.CalledProcessError:
        LOG.debug("facilities vacuum skipped (table may not exist yet)")


def _swap(env: dict[str, str], *, pbf_filename: str, sha: str, started_at: dt.datetime) -> None:
    """Run the atomic swap + etl_runs INSERT.

    Counts and timing come from the SQL file via psql variables. The Redis
    cache flush is dispatched separately by ``_flush_redis_cache`` after this
    function returns (per design D9 the flush must happen AFTER the COMMIT).
    """
    ways_count = int(_psql(env, f"SELECT count(*) FROM {STAGING_SCHEMA}.ways;", capture=True) or "0")
    vertices_count = int(
        _psql(env, f"SELECT count(*) FROM {STAGING_SCHEMA}.ways_vertices_pgr;", capture=True)
        or "0"
    )
    completed_at = dt.datetime.now(dt.UTC)
    LOG.info(
        "atomic schema swap starting",
        extra={
            "ways_count": ways_count,
            "vertices_count": vertices_count,
            "elapsed_s": round((completed_at - started_at).total_seconds(), 1),
        },
    )
    try:
        _psql_file(
            env,
            SWAP_SQL,
            variables={
                "live_schema": LIVE_SCHEMA,
                "staging_schema": STAGING_SCHEMA,
                "prev_schema": PREV_SCHEMA,
                "pbf_filename": pbf_filename,
                "pbf_sha256": sha,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "ways_count": str(ways_count),
                "vertices_count": str(vertices_count),
            },
        )
    except subprocess.CalledProcessError as exc:
        LOG.error("swap failed; live schema untouched", extra={"exit_code": exc.returncode})
        sys.exit(5)
    LOG.info("swap complete; live schema is fresh")


def _flush_redis_cache() -> None:
    """Delete every Redis key matching ``cf:*``.

    Best-effort: a flush failure is logged but does NOT fail the ETL (the data
    is already correct in Postgres; stale cache entries will eventually expire
    via TTL, and the next function_version bump invalidates them deterministically).

    Uses ``redis-cli --scan`` so the operation is O(keys) without blocking the
    server like ``KEYS *`` would.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        LOG.warning("REDIS_URL unset; skipping cf:* flush")
        return

    LOG.info("flushing Redis cf:* namespace", extra={"redis_url": _redact_url(redis_url)})
    # `redis-cli` ships in the alpine base image of the redis service container,
    # but NOT in the ETL container. So we run it via `docker exec` from the host?
    # No — keep it simple: pipe SCAN | DEL through a single subprocess pipeline
    # using the Python redis client would add a dep. Compromise: use a tiny
    # inline python helper that talks Redis protocol via the stdlib socket.
    try:
        _flush_via_socket(redis_url)
    except Exception as exc:  # noqa: BLE001 — best-effort
        LOG.warning("cache flush failed (non-fatal)", extra={"error": str(exc)})


def _redact_url(url: str) -> str:
    """Redact password from a redis URL for logging."""
    if "@" not in url:
        return url
    scheme_creds, _, host_part = url.rpartition("@")
    scheme, _, _ = scheme_creds.partition("://")
    return f"{scheme}://***@{host_part}"


def _flush_via_socket(redis_url: str) -> None:
    """Minimal RESP client: SCAN + DEL for the cf:* namespace.

    Avoids adding a real redis-py dependency to the ETL container's tiny
    surface. Supports the dialect ``redis://[:password@]host[:port][/db]``.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(redis_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    password = parsed.password
    db = int((parsed.path or "/0").lstrip("/") or "0")

    def send(sock: socket.socket, *args: str) -> bytes:
        payload = f"*{len(args)}\r\n" + "".join(f"${len(a)}\r\n{a}\r\n" for a in args)
        sock.sendall(payload.encode("utf-8"))
        return _read_response(sock)

    def _read_response(sock: socket.socket) -> bytes:
        buf = b""
        while not buf.endswith(b"\r\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf

    with socket.create_connection((host, port), timeout=5) as sock:
        if password:
            send(sock, "AUTH", password)
        send(sock, "SELECT", str(db))

        # SCAN cursor + cf:* match; DEL each batch.
        cursor = "0"
        total_deleted = 0
        while True:
            resp = send(sock, "SCAN", cursor, "MATCH", "cf:*", "COUNT", "500").decode("utf-8")
            # Minimal RESP parse for an array of [cursor, [keys...]].
            cursor, keys = _parse_scan(resp)
            if keys:
                send(sock, "DEL", *keys)
                total_deleted += len(keys)
            if cursor == "0":
                break
        LOG.info("cf:* flush done", extra={"deleted": total_deleted})


def _parse_scan(resp: str) -> tuple[str, list[str]]:
    """Tiny RESP parser sufficient for a SCAN reply: ``*2\\r\\n$<n>\\r\\n<cursor>\\r\\n*<m>\\r\\n...``."""
    lines = resp.split("\r\n")
    # lines[0] is the outer array marker '*2'; lines[1] is '$<n>'; lines[2] is the cursor
    cursor = lines[2] if len(lines) > 2 else "0"
    # lines[3] is '*<m>' for the inner array; subsequent pairs are $<len>\r\n<key>
    keys: list[str] = []
    if len(lines) > 3 and lines[3].startswith("*"):
        idx = 4
        while idx + 1 < len(lines):
            if not lines[idx].startswith("$"):
                break
            keys.append(lines[idx + 1])
            idx += 2
    return cursor, keys


@contextmanager
def _timed(stage: str):
    t0 = time.monotonic()
    LOG.info("stage start", extra={"stage": stage})
    try:
        yield
    finally:
        LOG.info("stage end", extra={"stage": stage, "elapsed_s": round(time.monotonic() - t0, 1)})


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="load_osm", description=__doc__.splitlines()[0])
    p.add_argument("--pbf", required=True, type=Path, help="absolute path to the .osm.pbf file")
    p.add_argument("--force", action="store_true",
                   help="bypass the sha256 idempotency check")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _parse_args(argv)

    if not args.pbf.exists():
        LOG.error("PBF not found", extra={"path": str(args.pbf)})
        return 2

    env = _db_env()
    _write_pgpass(env)
    started_at = dt.datetime.now(dt.UTC)

    with _timed("sha256"):
        sha = _sha256_file(args.pbf)
    LOG.info("pbf sha256", extra={"sha256": sha, "pbf": args.pbf.name})

    if not args.force and _is_already_loaded(env, sha):
        LOG.info("already loaded; nothing to do", extra={"sha256": sha})
        return 0

    with _timed("pgr_version_check"):
        _assert_pgr_version(env)

    with _timed("recreate_staging"):
        _drop_and_recreate_staging(env)

    with _timed("osm2pgsql"):
        _osm2pgsql(env, args.pbf)

    with _timed("node_network"):
        _node_network(env)

    with _timed("phase2_hooks"):
        _apply_phase2_hooks(env)

    with _timed("vacuum_analyze"):
        _vacuum_analyze_staging(env)

    with _timed("swap"):
        _swap(env, pbf_filename=args.pbf.name, sha=sha, started_at=started_at)

    with _timed("cache_flush"):
        _flush_redis_cache()

    LOG.info(
        "ETL complete",
        extra={"total_elapsed_s": round((dt.datetime.now(dt.UTC) - started_at).total_seconds(), 1)},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
