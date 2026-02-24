-- Migration 087: Fix team delete to also clean up libraries and resources
-- Previously missed: libraries (soft-ref), resources linked via resource_items

CREATE OR REPLACE FUNCTION delete_team_with_cleanup(target_team_id BIGINT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  team_owner_id UUID;
  caller_id UUID;
  tid TEXT;
BEGIN
  -- Get caller
  caller_id := auth.uid();
  IF caller_id IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  -- Verify team exists and caller is owner
  SELECT owner_id INTO team_owner_id
    FROM teams WHERE id = target_team_id;

  IF team_owner_id IS NULL THEN
    RAISE EXCEPTION 'Team not found';
  END IF;

  IF team_owner_id != caller_id THEN
    RAISE EXCEPTION 'Only the team owner can delete a team';
  END IF;

  -- Cast to text for scope_id comparisons
  tid := target_team_id::TEXT;

  -- 1. Delete resources scoped to this team (CASCADE cleans up:
  --    resource_versions, resource_tags, resource_items, resource_access_logs,
  --    shares, task_assets, resource_summaries, resource_transcripts,
  --    resource_analysis, review_comments, review_status)
  DELETE FROM resources
    WHERE id IN (
      SELECT resource_id FROM resource_items
      WHERE scope_type = 'team' AND scope_id = tid
    );

  -- 2. Delete resource_tags for tags owned by this team
  DELETE FROM resource_tags
    WHERE tag_id IN (
      SELECT id FROM tags WHERE scope_type = 'team' AND scope_id = tid
    );

  -- 3. Delete soft-reference tables (scope_type + scope_id pattern)
  DELETE FROM tags              WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM smart_collections WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM resource_items    WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM folders           WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM libraries         WHERE scope_type = 'team' AND scope_id = tid;

  -- 4. Delete projects belonging to this team
  DELETE FROM projects WHERE team_id = target_team_id;

  -- 5. Delete collections belonging to this team
  DELETE FROM collections WHERE team_id = target_team_id;

  -- 6. Delete the team row (CASCADE handles: team_members, team_invites,
  --    team_quotas, member_quotas, point_transactions, orders,
  --    project_workflows, team_plans, notifications)
  DELETE FROM teams WHERE id = target_team_id;
END;
$$;
