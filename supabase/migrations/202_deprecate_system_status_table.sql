-- 202_deprecate_system_status_table.sql
--
-- The `update_system_status_workflow` (scheduled_health.py) used to upsert
-- a single row in the `system_status` table every 30 seconds — about
-- 43,200 writes/day on a hot row, plus a Realtime broadcast on every
-- write that fanout to all subscribed admin clients. Most ticks didn't
-- change anything meaningful (storage_free_gb doesn't move every 30s),
-- so 99% of those writes + broadcasts were waste.
--
-- A2 of the A-route plan migrates the snapshot to Redis (HASH + pubsub
-- in app/services/system_status_redis.py). The new collector writes
-- there; the API endpoint reads there; subscribers fire only on
-- meaningful change.
--
-- This migration:
--   * Adds a "deprecated" comment to the table so anyone querying it
--     directly (psql / admin) sees the warning.
--   * DOES NOT drop the table yet — keep it 30 days for any external
--     reader (BI dashboards / Grafana / etc.) to migrate. Drop in a
--     follow-up migration after operator confirms no consumers.
--
-- Note: we can't actually disable writes to the table from SQL (the
-- application code wrote here); the writer side is changed in
-- app/workflows/scheduled_health.py (this PR) so the table stops
-- receiving updates the moment the new code deploys. Existing rows
-- stay until the follow-up DROP.

COMMENT ON TABLE public.system_status IS
    'DEPRECATED 2026-05-04 (A-route A2). Snapshot moved to Redis HASH
     mediahub:system:status (see app/services/system_status_redis.py).
     Table no longer receives writes. Will be dropped in a follow-up
     migration after the 30-day deprecation window. Read from /api/v1/
     system/status (Redis-backed) instead.';
