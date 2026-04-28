-- 174: PR-D2.2 — let mediahub_dbos SET ROLE service_role
--
-- The canonical pattern (validated PoC #11) for DBOS workflow steps writing
-- to public.* tables is:
--     conn = psycopg.connect(<mediahub_dbos creds>)
--     conn.execute("SET ROLE service_role")  -- elevate to bypass RLS
--     conn.execute("INSERT/UPDATE/DELETE ...")
--
-- For SET ROLE service_role to succeed, mediahub_dbos must have membership
-- in service_role. PoC #11 happened to use postgres.heygo-dev which already
-- has the chain; production DBOS connects as mediahub_dbos directly and the
-- chain isn't there by default.
--
-- Discovered during PR-D2.2 smoke test of execute_issue → load_issue step:
-- "permission denied to set role service_role" from psycopg.

GRANT service_role TO mediahub_dbos;

-- Verify (visible in supabase log):
-- SELECT pg_has_role('mediahub_dbos', 'service_role', 'MEMBER') → t
COMMENT ON ROLE mediahub_dbos IS
  'DBOS Transact runtime role (CREATEDB, no BYPASSRLS). Member of service_role since 2026-04-28 (migration 174) so DBOS step bodies can `SET ROLE service_role` to bypass RLS — canonical pattern from PoC #11.';
