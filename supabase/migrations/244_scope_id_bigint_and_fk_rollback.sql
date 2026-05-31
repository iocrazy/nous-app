-- Rollback for migration 244. (CI auto-detect skips *_rollback.sql.)
-- Reverts scope_id bigint→text + drops the FKs + restores the 10 policies to
-- the get_user_team_ids_text() (text) form. No data loss (bigint→text is exact).

-- 1. Drop the FKs.
ALTER TABLE public.resource_items    DROP CONSTRAINT IF EXISTS resource_items_scope_id_fkey;
ALTER TABLE public.folders           DROP CONSTRAINT IF EXISTS folders_scope_id_fkey;
ALTER TABLE public.tags              DROP CONSTRAINT IF EXISTS tags_scope_id_fkey;
ALTER TABLE public.smart_collections DROP CONSTRAINT IF EXISTS smart_collections_scope_id_fkey;

-- 2. Drop the bigint-fn policies.
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

-- 3. Convert scope_id bigint → text.
ALTER TABLE public.resource_items    ALTER COLUMN scope_id TYPE text USING scope_id::text;
ALTER TABLE public.folders           ALTER COLUMN scope_id TYPE text USING scope_id::text;
ALTER TABLE public.tags              ALTER COLUMN scope_id TYPE text USING scope_id::text;
ALTER TABLE public.smart_collections ALTER COLUMN scope_id TYPE text USING scope_id::text;

-- 4. Restore the policies using get_user_team_ids_text().
CREATE POLICY "Users can create folders" ON public.folders
  FOR INSERT WITH CHECK ((created_by = (SELECT auth.uid())) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))));
CREATE POLICY "Users can delete own scope folders" ON public.folders
  FOR DELETE USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
CREATE POLICY "Users can read own folders" ON public.folders
  FOR SELECT USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
CREATE POLICY "Users can update own scope folders" ON public.folders
  FOR UPDATE USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))))
            WITH CHECK (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
CREATE POLICY "Users can create resource items" ON public.resource_items
  FOR INSERT WITH CHECK ((added_by = (SELECT auth.uid())) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))));
CREATE POLICY "Users can delete resource items in scope" ON public.resource_items
  FOR DELETE USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
CREATE POLICY "Users can read resource items in scope" ON public.resource_items
  FOR SELECT USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
CREATE POLICY "Users can update resource items in scope" ON public.resource_items
  FOR UPDATE USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))))
            WITH CHECK (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
CREATE POLICY tags_select ON public.tags
  FOR SELECT USING (((type)::text = ANY (ARRAY['system'::text, 'time'::text]))
                    OR (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))))
                    OR (user_id = (SELECT auth.uid())));
CREATE POLICY "Users can read own resources" ON public.resources
  FOR SELECT USING ((creator_id = (SELECT auth.uid()))
                    OR (id IN (SELECT resource_items.resource_id FROM public.resource_items
                               WHERE resource_items.scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))))));
