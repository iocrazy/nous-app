-- Migration 149: wrap pg_try_advisory_lock / pg_advisory_unlock as callable
-- via PostgREST RPC.
--
-- Why: the agent_runs sweeper (PR #58, app/tasks/agent_runs_sweeper.py)
-- needs Postgres advisory locks for leader election across Celery workers.
-- It calls these via supabase.rpc("pg_try_advisory_lock", {"key": ...})
-- but PostgREST only exposes functions declared in the public schema with
-- matching parameter names — built-in pg_catalog functions aren't reachable
-- through RPC. Result: 285 WARNING lines / 10 minutes in prod, sweeper
-- never acquires the lock, heartbeat_lost + budget pause transitions never
-- fire.
--
-- Fix: two thin SECURITY DEFINER wrappers in public schema with a named
-- `lock_key` BIGINT parameter that PostgREST can resolve. They simply
-- delegate to the built-ins. SECURITY DEFINER is safe here because the
-- wrappers take a plain BIGINT and the advisory-lock primitive itself
-- has no access-control attack surface (it's per-session, numeric-only).

CREATE OR REPLACE FUNCTION public.try_advisory_lock(lock_key BIGINT)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT pg_try_advisory_lock(lock_key);
$$;

CREATE OR REPLACE FUNCTION public.advisory_unlock(lock_key BIGINT)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT pg_advisory_unlock(lock_key);
$$;

-- Only the service_role needs to call these (Celery workers). Revoke from
-- anon/authenticated so a compromised client session can't flood lock
-- acquisition and starve the sweeper.
REVOKE ALL ON FUNCTION public.try_advisory_lock(BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.advisory_unlock(BIGINT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.try_advisory_lock(BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.advisory_unlock(BIGINT) TO service_role;
