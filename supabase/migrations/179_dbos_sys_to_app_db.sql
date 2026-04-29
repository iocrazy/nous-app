-- 179_dbos_sys_to_app_db.sql
--
-- DBOS sys-DB co-located in app DB (configured in code via DBOSConfig
-- application_database_url + system_database_url both pointing at the
-- same `postgres` database — see app/services/dbos_orchestrator.py
-- init_dbos()). On first boot DBOS runs its own migrations 3-13+ to
-- create dbos.workflow_status / operation_outputs / events / etc. in
-- the app DB's `dbos` schema (was `<db>_dbos_sys.dbos` before).
--
-- This migration adds the user-isolation + Realtime plumbing on top:
--
--   1. RLS on dbos.workflow_status — users see only rows where
--      authenticated_user matches auth.uid() (DBOS sets this via
--      DBOSContextSetAuth in start_workflow_routed).
--   2. Realtime publication ADD — frontend Task Center can now
--      subscribe to status transitions natively.
--   3. SELECT grant + USAGE on schema for the authenticated role.
--
-- ──────────────────────────────────────────────────────────
-- IMPORTANT — execute as TWO different roles:
--
--   Part A (RLS + GRANTs): run as the dbos schema owner
--     (`mediahub_dbos` in our setup, or whoever owns the dbos
--     schema after DBOS migrations).
--
--   Part B (publication ADD): run as a postgres-superuser-equivalent
--     (`postgres` role, or via Supabase Studio SQL editor). The
--     supabase_realtime publication is owned by `postgres`, and
--     ALTER PUBLICATION requires being its owner — service_role
--     does NOT inherit that.
--
-- ──────────────────────────────────────────────────────────
-- Part A — owner: mediahub_dbos (or current dbos schema owner)
-- ──────────────────────────────────────────────────────────

ALTER TABLE dbos.workflow_status ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "wf_status_user_isolation" ON dbos.workflow_status;
CREATE POLICY "wf_status_user_isolation"
  ON dbos.workflow_status
  FOR SELECT
  TO authenticated
  USING (authenticated_user = (auth.uid())::text);

GRANT USAGE ON SCHEMA dbos TO authenticated;
GRANT SELECT ON dbos.workflow_status TO authenticated;

-- ──────────────────────────────────────────────────────────
-- Part B — owner: postgres (must be run via the postgres role,
-- e.g. psql 'postgresql://postgres.<tenant>@host:port/postgres'
-- or Supabase Studio SQL editor as superuser).
-- ──────────────────────────────────────────────────────────

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE dbos.workflow_status;
EXCEPTION WHEN duplicate_object THEN
  RAISE NOTICE 'dbos.workflow_status already in supabase_realtime publication';
END$$;
