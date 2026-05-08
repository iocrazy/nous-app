-- 211_dbos_workflow_status_rls.sql
--
-- ⚠️  SECURITY HARDENING — backfill missing RLS policy on prod
--
-- 5/7 sb-mediahub → mediahub-sb-prod migration didn't preserve RLS
-- on dbos.workflow_status:
--   dev:  relrowsecurity = TRUE,  policy `wf_status_user_isolation` enforces
--         authenticated_user = auth.uid()::text on SELECT.
--   prod: relrowsecurity = FALSE, no policy. Any authenticated user
--         could read every other user's workflow rows (input args,
--         output, error message) IF the dbos schema is exposed via
--         supabase REST. Currently it isn't exposed by default, but
--         the table being un-locked is a footgun the moment someone
--         flips the schema-exposure switch.
--
-- This migration enables RLS and recreates the policy verbatim from
-- dev (PERMISSIVE FOR SELECT TO authenticated USING
-- authenticated_user = auth.uid()::text). DBOS workflows store the
-- caller's UUID string in `authenticated_user` when launched via
-- DBOS.set_authentication; that's the column the policy keys off.
--
-- Idempotent: DROP POLICY IF EXISTS + CREATE POLICY, ENABLE ROW
-- LEVEL SECURITY is no-op if already on. Wrapped in DO block so this
-- doesn't hard-fail on a fresh DB where dbos.workflow_status hasn't
-- been materialised yet.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'dbos' AND table_name = 'workflow_status'
  ) THEN
    RAISE NOTICE 'dbos.workflow_status does not exist yet — re-run after DBOS init.';
    RETURN;
  END IF;

  -- Enable RLS (no-op if already on).
  ALTER TABLE dbos.workflow_status ENABLE ROW LEVEL SECURITY;

  -- Recreate the policy. PostgreSQL has no CREATE OR REPLACE POLICY,
  -- so we drop-then-create to remain idempotent.
  DROP POLICY IF EXISTS wf_status_user_isolation ON dbos.workflow_status;

  CREATE POLICY wf_status_user_isolation ON dbos.workflow_status
    AS PERMISSIVE
    FOR SELECT
    TO authenticated
    USING (authenticated_user = (auth.uid())::text);

  RAISE NOTICE 'wf_status_user_isolation policy installed on dbos.workflow_status.';
END
$$;
