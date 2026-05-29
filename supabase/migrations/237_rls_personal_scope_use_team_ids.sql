-- supabase/migrations/237_rls_personal_scope_use_team_ids.sql
-- Fix RLS policies that still compare scope_id to auth.uid() for personal scope.
--
-- PR-C (mig 234) remapped scope_id on resource_items / folders / tags /
-- smart_collections from auth.user_uuid to the personal-team snowflake.
-- However the RLS policies on resource_items and folders still had the
-- old branch:
--
--   ((scope_type='personal') AND (scope_id = auth.uid()::text)) OR
--   ((scope_type='team')     AND (scope_id IN get_user_team_ids_text))
--
-- After PR-C the LHS (personal branch) never matched — every personal
-- row's scope_id was a snowflake, not a UUID. RLS quietly returned 0
-- rows for personal-scope reads even though the data was intact.
--
-- Fix: collapse the two branches. Since PR-A guarantees every personal
-- team is in the user's team_members, `get_user_team_ids_text(auth.uid())`
-- already includes their personal team snowflake. So the policy can
-- simply check `scope_id IN team_ids` regardless of scope_type.
--
-- This is the RLS half of what PR-E will eventually finish (collapse
-- personal/team branches everywhere).

BEGIN;

-- resource_items: 3 policies
ALTER POLICY "Users can read resource items in scope" ON public.resource_items
  USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
ALTER POLICY "Users can update resource items in scope" ON public.resource_items
  USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
ALTER POLICY "Users can delete resource items in scope" ON public.resource_items
  USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));

-- resources: read policy (subquery still references the old shape)
ALTER POLICY "Users can read own resources" ON public.resources
  USING (
    creator_id = (SELECT auth.uid())
    OR id IN (
      SELECT resource_id FROM public.resource_items
       WHERE scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))
    )
  );

-- folders: 3 policies
ALTER POLICY "Users can read own folders" ON public.folders
  USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
ALTER POLICY "Users can update own scope folders" ON public.folders
  USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));
ALTER POLICY "Users can delete own scope folders" ON public.folders
  USING (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))));

COMMIT;
