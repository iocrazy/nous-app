-- Migration 244: PR-E Phase 4c-3 — scope_id text→bigint + FK to teams(id)
--
-- Final (optional) step of PR-E. scope_id on resource_items/folders/tags/
-- smart_collections holds a teams.id value but as `text`. Convert to `bigint`
-- (matching teams.id) and add a real FK so orphan scope_ids become impossible
-- and team deletion cascades cleanly.
--
-- Frontend is safe with no changes: bigIntSafeFetch (supabaseClient.ts) rewrites
-- all 16+ digit integers in PostgREST JSON to strings before parse, so scope_id
-- arrives as a string whether the column is text or bigint (same as teams.id,
-- already bigint).
--
-- RLS: 10 policies filter these tables' scope_id via get_user_team_ids_text()
-- (SETOF text). Once scope_id is bigint, `bigint IN (text)` fails, so each is
-- recreated using get_user_team_ids() (SETOF bigint, already present). The 4
-- libraries policies are untouched — they don't use get_user_team_ids_text
-- (they do `team_members.team_id = libraries.scope_id::bigint`), and
-- libraries.scope_id stays text.
--
-- Verified on prod 2026-05-30 (BEGIN/ROLLBACK, ON_ERROR_STOP=1): all 4 columns
-- become bigint, 4 FKs added, 0 policies left on get_user_team_ids_text, 4
-- libraries policies intact. scope_id values are all numeric (0 non-numeric;
-- tags has 81 NULLs which bigint + FK both allow). pg_dump pre-image:
-- NAS /volume1/docker/datahub/mediahub-sb-prod/pre_4c2b_20260530_091544.sql

-- 1. Drop the 10 policies that reference scope_id via get_user_team_ids_text()
--    (a column can't change type while a policy depends on it).
DROP POLICY "Users can create folders" ON public.folders;
DROP POLICY "Users can delete own scope folders" ON public.folders;
DROP POLICY "Users can read own folders" ON public.folders;
DROP POLICY "Users can update own scope folders" ON public.folders;
DROP POLICY "Users can create resource items" ON public.resource_items;
DROP POLICY "Users can delete resource items in scope" ON public.resource_items;
DROP POLICY "Users can read resource items in scope" ON public.resource_items;
DROP POLICY "Users can update resource items in scope" ON public.resource_items;
DROP POLICY tags_select ON public.tags;
DROP POLICY "Users can read own resources" ON public.resources;

-- 2. Convert scope_id text → bigint.
ALTER TABLE public.resource_items    ALTER COLUMN scope_id TYPE bigint USING scope_id::bigint;
ALTER TABLE public.folders           ALTER COLUMN scope_id TYPE bigint USING scope_id::bigint;
ALTER TABLE public.tags              ALTER COLUMN scope_id TYPE bigint USING scope_id::bigint;
ALTER TABLE public.smart_collections ALTER COLUMN scope_id TYPE bigint USING scope_id::bigint;

-- 3. Recreate the 10 policies using the bigint get_user_team_ids().
CREATE POLICY "Users can create folders" ON public.folders
  FOR INSERT WITH CHECK ((created_by = (SELECT auth.uid())) AND (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid())))));
CREATE POLICY "Users can delete own scope folders" ON public.folders
  FOR DELETE USING (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))));
CREATE POLICY "Users can read own folders" ON public.folders
  FOR SELECT USING (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))));
CREATE POLICY "Users can update own scope folders" ON public.folders
  FOR UPDATE USING (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
            WITH CHECK (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))));
CREATE POLICY "Users can create resource items" ON public.resource_items
  FOR INSERT WITH CHECK ((added_by = (SELECT auth.uid())) AND (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid())))));
CREATE POLICY "Users can delete resource items in scope" ON public.resource_items
  FOR DELETE USING (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))));
CREATE POLICY "Users can read resource items in scope" ON public.resource_items
  FOR SELECT USING (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))));
CREATE POLICY "Users can update resource items in scope" ON public.resource_items
  FOR UPDATE USING (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
            WITH CHECK (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))));
CREATE POLICY tags_select ON public.tags
  FOR SELECT USING (((type)::text = ANY (ARRAY['system'::text, 'time'::text]))
                    OR (scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
                    OR (user_id = (SELECT auth.uid())));
CREATE POLICY "Users can read own resources" ON public.resources
  FOR SELECT USING ((creator_id = (SELECT auth.uid()))
                    OR (id IN (SELECT resource_items.resource_id FROM public.resource_items
                               WHERE resource_items.scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))));

-- 4. Add the scope_id → teams(id) FK with ON DELETE CASCADE.
ALTER TABLE public.resource_items
  ADD CONSTRAINT resource_items_scope_id_fkey
  FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;
ALTER TABLE public.folders
  ADD CONSTRAINT folders_scope_id_fkey
  FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;
ALTER TABLE public.tags
  ADD CONSTRAINT tags_scope_id_fkey
  FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;
ALTER TABLE public.smart_collections
  ADD CONSTRAINT smart_collections_scope_id_fkey
  FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;
