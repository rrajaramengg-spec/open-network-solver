-- Deferred cleanup of the routing_prev schema.
--
-- Run via:
--   psql -v retention_hours=24 -f drop_old_routing.sql
--
-- The check ensures we never drop a routing_prev that is younger than the
-- retention window, providing an operator-friendly "instant rollback" capability.
-- If the most recent ETL run is younger than `retention_hours`, the script
-- is a no-op.

\set ON_ERROR_STOP on

DO $$
DECLARE
    last_completed_at timestamptz;
    retention_interval interval := make_interval(hours => :retention_hours);
BEGIN
    SELECT max(completed_at) INTO last_completed_at FROM etl_runs;
    IF last_completed_at IS NULL THEN
        RAISE NOTICE 'no etl_runs rows yet; not dropping routing_prev';
        RETURN;
    END IF;
    IF (now() - last_completed_at) < retention_interval THEN
        RAISE NOTICE 'last ETL was % ago (< retention %); keeping routing_prev',
            now() - last_completed_at, retention_interval;
        RETURN;
    END IF;
    EXECUTE 'DROP SCHEMA IF EXISTS routing_prev CASCADE';
    RAISE NOTICE 'dropped routing_prev (retained for >= % after last ETL)', retention_interval;
END
$$;
