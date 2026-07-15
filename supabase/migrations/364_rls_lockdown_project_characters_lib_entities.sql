-- 364_rls_lockdown_project_characters_lib_entities.sql
--
-- RLS lockdown for project_characters (mig 357) and project_lib_entities
-- (mig 358). Both tables were created WITHOUT enabling RLS and with no
-- policies, while their closing NOTIFY pgrst exposed them through the
-- PostgREST anon API — the world-readable-table class of bug (see 265's
-- retroactive log-table lockdown; sibling migrations 290/307/351/356 all
-- guarded against exactly this and these two were missed).
--
-- Both tables are backend-only: every access path goes through FastAPI with
-- the service-role client (project_character_repository /
-- project_lib_entity_repository), and the frontend never touches them via
-- supabase-js. So the service-role-only pattern from
-- 351_distribution_accounts.sql is the correct, zero-behavior-change fix.

BEGIN;

ALTER TABLE public.project_characters ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on project_characters" ON public.project_characters;
CREATE POLICY "Service role full access on project_characters" ON public.project_characters FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.project_lib_entities ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on project_lib_entities" ON public.project_lib_entities;
CREATE POLICY "Service role full access on project_lib_entities" ON public.project_lib_entities FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
