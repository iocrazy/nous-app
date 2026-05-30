-- Migration 242: PR-E Phase 4c-1b — drop scope_type from RLS policies
--
-- Five RLS policies still reference scope_type in a
--   (scope_type='personal' AND scope_id=auth.uid())
--    OR (scope_type='team' AND scope_id IN get_user_team_ids_text(auth.uid()))
-- shape. Post Spec-1 PR-C, scope_id is always a teams.id snowflake and the
-- user's personal team is included in get_user_team_ids_text(), so the whole
-- thing collapses to `scope_id IN get_user_team_ids_text(auth.uid())` — exactly
-- what mig 237 already did for the resource_items/folders SELECT/UPDATE *USING*
-- clauses (this finishes the job for the INSERT/UPDATE WITH CHECK clauses and
-- the tags SELECT policy that 237 missed).
--
-- This removes the last dependency on the scope_type column from RLS, so a
-- later migration can DROP COLUMN scope_type without being blocked.
--
-- Verified on prod 2026-05-30 (BEGIN/ROLLBACK, ON_ERROR_STOP): all 5 ALTERs
-- succeed and afterwards 0 policies reference scope_type. Behaviourally
-- equivalent (scope_id IN team_ids covers both personal and team post PR-C).

ALTER POLICY "Users can create folders" ON public.folders
  WITH CHECK (
    created_by = (SELECT auth.uid())
    AND scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))
  );

ALTER POLICY "Users can update own scope folders" ON public.folders
  WITH CHECK (
    scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))
  );

ALTER POLICY "Users can create resource items" ON public.resource_items
  WITH CHECK (
    added_by = (SELECT auth.uid())
    AND scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))
  );

ALTER POLICY "Users can update resource items in scope" ON public.resource_items
  WITH CHECK (
    scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))
  );

ALTER POLICY "tags_select" ON public.tags
  USING (
    ((type)::text = ANY (ARRAY['system'::text, 'time'::text]))
    OR scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))
    OR user_id = (SELECT auth.uid())
  );
