-- 065_resource_items_update_policy.sql
-- Fix: Add missing UPDATE policy for resource_items table
-- Without this policy, moveResourceItem() silently fails because RLS blocks the UPDATE

CREATE POLICY "Users can update resource items in scope"
  ON resource_items FOR UPDATE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  )
  WITH CHECK (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );
