-- Lock down 8 backend-only tables that any authenticated user (or anon) could
-- read — and in 3 cases write/delete/TRUNCATE — directly via the publishable key.
--
-- Found by a behavioral RLS audit (2026-06-07): impersonate a fresh user at the
-- DB level (SET ROLE authenticated + injected request.jwt.claims) and observe
-- the rows actually visible. Result on prod: a "nobody" user could read ALL of
-- application_logs (1.9M), api_request_logs (300k), frontend_error_logs,
-- system_settings, dbos_workflow_routing; and boundary_audit / task_flows /
-- issue_sequence had RLS DISABLED entirely with a blanket GRANT ALL to
-- anon+authenticated (so any logged-in user could even DELETE/TRUNCATE them).
--
-- Why this is safe (verified):
--   * Backend reads/writes these via service_role / the postgres pooler role,
--     both rolbypassrls=true — unaffected by RLS or by REVOKE FROM anon/authenticated.
--   * No frontend or admin app reads ANY of these via supabase-js (grep-verified);
--     all access goes through the backend admin API (service_role).
--   * Trigger/function writers checked: issue_sequence writers are SECURITY
--     DEFINER (run as postgres); task_flows' INVOKER trigger fires only on
--     task_tracking, which is backend-only-written; boundary_audit is written by
--     backend app code. None run in an authenticated-user context.
--   * frontend_error_logs DOES accept anon/authenticated INSERT (the browser logs
--     errors) — those INSERT policies + privilege are preserved; only the
--     all-authenticated SELECT leak is removed.
--
-- This changes ONLY the frontend (anon/authenticated key) path. Admins still see
-- all logs because the admin pages read through the backend (service_role).
-- Idempotent.

-- ── Group 1: RLS-ON tables — drop the over-permissive SELECT policy ───────────
-- (writes were already denied: no write policy existed, so RLS blocked them.)

DROP POLICY IF EXISTS "authenticated_select"           ON public.application_logs;
DROP POLICY IF EXISTS "admin_read_api_request_logs"    ON public.api_request_logs;
DROP POLICY IF EXISTS "admin_read_frontend_error_logs" ON public.frontend_error_logs;
DROP POLICY IF EXISTS "dbos_routing_select"            ON public.dbos_workflow_routing;

-- system_settings: replace the all-authenticated SELECT with admin-only, matching
-- the table's existing admin insert/update/delete policies. Backend (service_role)
-- bypasses RLS regardless; this only governs the direct authenticated-key path.
DROP POLICY IF EXISTS "system_settings_select" ON public.system_settings;
DROP POLICY IF EXISTS "system_settings_admin_select" ON public.system_settings;
CREATE POLICY "system_settings_admin_select"
    ON public.system_settings
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.user_profiles up
            WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::public.user_role
        )
    );

-- ── Group 2: RLS-OFF tables — enable RLS (deny-all, no policy) + revoke the
-- blanket grant so anon/authenticated cannot read, write, or TRUNCATE them.
-- bypassrls backend roles (service_role / postgres) are unaffected. ───────────

ALTER TABLE public.boundary_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.boundary_audit FROM anon, authenticated;

ALTER TABLE public.task_flows ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.task_flows FROM anon, authenticated;

ALTER TABLE public.issue_sequence ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.issue_sequence FROM anon, authenticated;

-- ── Belt-and-braces: RLS does NOT gate TRUNCATE/REFERENCES/TRIGGER (table-level
-- privileges), so revoke those on the RLS-ON backend-only log/config tables too.
-- (SELECT/INSERT/UPDATE/DELETE stay grant+RLS-gated; frontend_error_logs keeps
-- its INSERT path via its INSERT policies + the retained INSERT privilege.) ───

REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.application_logs      FROM anon, authenticated;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.api_request_logs      FROM anon, authenticated;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.dbos_workflow_routing FROM anon, authenticated;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.system_settings       FROM anon, authenticated;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.frontend_error_logs   FROM anon, authenticated;

-- PostgREST must drop its policy plan cache, not just reload the schema.
-- (A plain NOTIFY is insufficient for ALTER POLICY / new policies — the prod
--  REST container also needs a restart; see runbook.)
NOTIFY pgrst, 'reload schema';
