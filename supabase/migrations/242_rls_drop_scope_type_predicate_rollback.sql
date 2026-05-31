-- Rollback for migration 242 — restore the scope_type predicate in the 5 RLS
-- policies. (CI auto-detect skips *_rollback.sql, so this never auto-applies.)
-- Only meaningful while the scope_type column still exists (i.e. before the
-- Phase 4c-2 DROP COLUMN). Reproduces the pre-242 definitions.

ALTER POLICY "Users can create folders" ON public.folders
  WITH CHECK (
    (created_by = (SELECT auth.uid()))
    AND (
      (((scope_type)::text = 'personal'::text) AND (scope_id = ((SELECT auth.uid()))::text))
      OR (((scope_type)::text = 'team'::text) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))))
    )
  );

ALTER POLICY "Users can update own scope folders" ON public.folders
  WITH CHECK (
    (((scope_type)::text = 'personal'::text) AND (scope_id = ((SELECT auth.uid()))::text))
    OR (((scope_type)::text = 'team'::text) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))))
  );

ALTER POLICY "Users can create resource items" ON public.resource_items
  WITH CHECK (
    (added_by = (SELECT auth.uid()))
    AND (
      (((scope_type)::text = 'personal'::text) AND (scope_id = ((SELECT auth.uid()))::text))
      OR (((scope_type)::text = 'team'::text) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))))
    )
  );

ALTER POLICY "Users can update resource items in scope" ON public.resource_items
  WITH CHECK (
    (((scope_type)::text = 'personal'::text) AND (scope_id = ((SELECT auth.uid()))::text))
    OR (((scope_type)::text = 'team'::text) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))))
  );

ALTER POLICY "tags_select" ON public.tags
  USING (
    ((type)::text = ANY (ARRAY['system'::text, 'time'::text]))
    OR (((scope_type)::text = 'team'::text) AND (scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid())))))
    OR (user_id = (SELECT auth.uid()))
  );
