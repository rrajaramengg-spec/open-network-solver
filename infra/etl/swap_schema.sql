-- =============================================================================
-- Atomic schema swap for the closest-facility routing ETL.
--
-- Design references:
--   * D9  — Atomic schema swap, NOT cache invalidation, for fresh-data semantics
--   * spec routing-network: "Atomic schema swap on success",
--                            "ETL failure leaves live schema untouched"
--   * spec closest-facility: "Cache is flushed immediately after the atomic
--                             schema swap"
--
-- Invoked from infra/etl/load_osm.py via psql with these variables:
--   :live_schema      e.g. 'routing'
--   :staging_schema   e.g. 'routing_next'
--   :prev_schema      e.g. 'routing_prev'
--   :pbf_filename
--   :pbf_sha256
--   :started_at       ISO-8601 UTC
--   :completed_at     ISO-8601 UTC
--   :ways_count       integer
--   :vertices_count   integer
--
-- Guarantees:
--   * The two ALTER SCHEMA statements run inside ONE transaction so no point
--     in time has the live name pointing at half-built tables.
--   * The etl_runs INSERT runs in the SAME transaction so we never end up in
--     a state where the data is live but the provenance row is missing.
--   * Redis cf:* flush runs AFTER COMMIT — even if the flush fails the data
--     is correct; stale cache entries will simply be re-validated against the
--     new schema at TTL expiry or via the function_version bump.
--   * routing_prev is RETAINED (not dropped) for at least 24 h so an
--     operator can manually swap back via:
--         BEGIN;
--         ALTER SCHEMA routing      RENAME TO routing_failed;
--         ALTER SCHEMA routing_prev RENAME TO routing;
--         COMMIT;
--     A separate `drop_old_routing.sql` (Phase 1.7 cleanup job) removes
--     routing_prev after the retention window.
-- =============================================================================

\set ON_ERROR_STOP on

-- On first load there is no live routing graph yet.  The alembic migration
-- pre-creates `routing.facilities` (table shape only), so checking just for
-- the namespace produces a false positive.  Detect via the ways table that
-- only the ETL creates.
SELECT EXISTS (
    SELECT 1
    FROM pg_class c
    JOIN pg_namespace n ON c.relnamespace = n.oid
    WHERE n.nspname = :'live_schema'
      AND c.relname = 'ways'
      AND c.relkind = 'r'
) AS live_exists \gset

\echo 'starting atomic schema swap: :staging_schema -> :live_schema'

BEGIN;

\if :live_exists
-- Steady-state: acquire exclusive locks so no in-flight query sees a
-- half-rotated schema, then retire the current live schema to prev.
LOCK TABLE :"live_schema".ways IN ACCESS EXCLUSIVE MODE NOWAIT;
LOCK TABLE :"live_schema".ways_vertices_pgr IN ACCESS EXCLUSIVE MODE NOWAIT;
DROP SCHEMA IF EXISTS :"prev_schema" CASCADE;
ALTER SCHEMA :"live_schema" RENAME TO :"prev_schema";
\else
-- First load: the alembic baseline may have created an empty `routing`
-- namespace (e.g. `routing.facilities` table shape).  Drop it so the
-- staging rename below can claim the name.
DROP SCHEMA IF EXISTS :"live_schema" CASCADE;
\endif

-- Step 2: promote staging to live.
ALTER SCHEMA :"staging_schema" RENAME TO :"live_schema";

-- Step 3: record provenance. Normal-run idempotency is enforced by the
-- Python-side pre-check (``_is_already_loaded``), which skips the whole ETL
-- before any work happens. A ``--force`` re-import (or an identical-content
-- refresh) deliberately bypasses that pre-check, so here we UPSERT on the
-- unique ``pbf_sha256`` key: the provenance row is updated in place rather
-- than rejected, keeping "full re-import with atomic swap" working for any
-- PBF, already-seen or not.
INSERT INTO etl_runs (
    pbf_filename,
    pbf_sha256,
    pbf_published_date,
    started_at,
    completed_at,
    ways_count,
    vertices_count
) VALUES (
    :'pbf_filename',
    :'pbf_sha256',
    NULL,  -- TODO(phase-2): parse from osmconvert --out-statistics
    :'started_at'::timestamptz,
    :'completed_at'::timestamptz,
    :'ways_count'::bigint,
    :'vertices_count'::bigint
)
ON CONFLICT (pbf_sha256) DO UPDATE SET
    pbf_filename   = EXCLUDED.pbf_filename,
    started_at     = EXCLUDED.started_at,
    completed_at   = EXCLUDED.completed_at,
    ways_count     = EXCLUDED.ways_count,
    vertices_count = EXCLUDED.vertices_count;

COMMIT;

\echo 'swap committed'

-- ---------------------------------------------------------------------------
-- Post-commit Redis cache flush.
--
-- Implementation note: we do NOT use pg_notify here, because the routing
-- service does not need to react synchronously (cache misses against the new
-- schema are correct, just slower). The flush is performed by the Python
-- orchestrator after this script returns, via a single ``redis-cli --scan
-- --pattern 'cf:*'`` + DEL pass. Phase 3 may upgrade this to a Redis
-- keyspace notification or an HTTP webhook into the service.
--
-- Closing this script with an echo so the orchestrator can log a marker
-- line and then dispatch the cache-flush step.
-- ---------------------------------------------------------------------------
\echo 'swap_schema.sql done; orchestrator will now flush Redis cf:*'
