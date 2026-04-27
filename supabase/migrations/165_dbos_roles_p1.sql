-- 165: DBOS migration P1 — dedicated roles for backend + DBOS
--
-- Context (eng review 2026-04-27):
-- mediahub backend currently uses service_role for ALL DB access via
-- BaseRepository._get_client → get_async_supabase_admin. service_role has
-- BYPASSRLS=true, so RLS policies on 98 public.* tables are not actually
-- enforced server-side. P1 of the role hardening plan introduces two
-- dedicated roles so DBOS and (eventually) the FastAPI backend stop sharing
-- service_role.
--
-- This migration is INTENDED to be applied before PR-D0 PoC #10/#11 so the
-- RLS verification can run against a realistic role.
--
-- Idempotent: safe to re-run.

-- =============================================================================
-- Role 1: mediahub_dbos
--   Purpose: DBOS Transact runtime connects with this role.
--   Privileges: CREATEDB (needed for postgres_dbos_sys), no BYPASSRLS.
--   DBOS workflow steps that must bypass RLS will use service_role explicitly
--   via supabase-py admin client (already the codebase pattern).
-- =============================================================================
-- Role created NOLOGIN so the migration is safe to commit. Operator must run
-- ALTER ROLE mediahub_dbos LOGIN PASSWORD '<secret>' before DBOS uses it
-- (rotate password via supabase dashboard or psql; never commit secret).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mediahub_dbos') THEN
    CREATE ROLE mediahub_dbos NOLOGIN
      CREATEDB
      NOSUPERUSER
      NOCREATEROLE
      NOBYPASSRLS;
  ELSE
    ALTER ROLE mediahub_dbos
      CREATEDB
      NOSUPERUSER
      NOCREATEROLE
      NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO mediahub_dbos;
-- Read access to existing tables so DBOS can call out to public.* when needed.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mediahub_dbos;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO mediahub_dbos;

-- DBOS metadata lives in a separate database (postgres_dbos_sys), so we do
-- NOT grant write on public.* here. Future migrations will grant per-table
-- write access for tables DBOS workflows directly mutate (e.g. issues).

-- =============================================================================
-- Role 2: mediahub_app
--   Purpose: FastAPI request-path code (router → service → repository) will
--   eventually run as this role via PostgREST role switching, so RLS
--   policies are actually enforced.
--   Privileges: NOLOGIN (assumed via authenticator), NOBYPASSRLS.
--   Migration plan P2 (separate PR) refactors BaseRepository to choose
--   between mediahub_app (user-path) and service_role (privileged-path).
-- =============================================================================
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mediahub_app') THEN
    CREATE ROLE mediahub_app NOLOGIN NOINHERIT NOBYPASSRLS;
  ELSE
    ALTER ROLE mediahub_app NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
END $$;

-- authenticator role already exists in supabase, allow it to SET ROLE
GRANT mediahub_app TO authenticator;
GRANT USAGE ON SCHEMA public TO mediahub_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mediahub_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mediahub_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mediahub_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO mediahub_app;

-- =============================================================================
-- Sanity outputs (visible in supabase dashboard query log).
-- =============================================================================
COMMENT ON ROLE mediahub_dbos IS
  'DBOS Transact runtime role (CREATEDB, no BYPASSRLS). Added 2026-04-27 by migration 165.';
COMMENT ON ROLE mediahub_app IS
  'FastAPI request-path role (NOLOGIN, no BYPASSRLS). Added 2026-04-27 by migration 165.';
