-- 010_fix_rls_recursion.sql
-- Fix infinite recursion in RLS policies for team_members table
-- The issue: team_members SELECT policy queries team_members itself, causing recursion

-- ============================================================================
-- Step 1: Create helper function to get user's team IDs (SECURITY DEFINER bypasses RLS)
-- ============================================================================

CREATE OR REPLACE FUNCTION get_user_team_ids(p_user_id UUID)
RETURNS SETOF UUID AS $$
BEGIN
  RETURN QUERY SELECT team_id FROM team_members WHERE user_id = p_user_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION get_user_team_ids(UUID) TO authenticated;

-- ============================================================================
-- Step 2: Drop and recreate team_members RLS policies
-- ============================================================================

DROP POLICY IF EXISTS "Members can view team members" ON team_members;
DROP POLICY IF EXISTS "Owners can add members" ON team_members;
DROP POLICY IF EXISTS "Owners can remove members" ON team_members;

-- Fixed: Use helper function instead of direct subquery
CREATE POLICY "Members can view team members" ON team_members
  FOR SELECT USING (
    team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

CREATE POLICY "Users can add themselves or owners can add" ON team_members
  FOR INSERT WITH CHECK (
    -- Users can add themselves (via invite code join)
    user_id = auth.uid()
    -- Or team owner can add members
    OR team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

CREATE POLICY "Users can leave or owners can remove" ON team_members
  FOR DELETE USING (
    -- Users can remove themselves (leave team)
    user_id = auth.uid()
    -- Or team owner can remove members
    OR team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- ============================================================================
-- Step 3: Update teams table policies to use helper function
-- ============================================================================

DROP POLICY IF EXISTS "Users can view teams they belong to" ON teams;

CREATE POLICY "Users can view teams they belong to" ON teams
  FOR SELECT USING (
    owner_id = auth.uid()
    OR id IN (SELECT get_user_team_ids(auth.uid()))
  );

-- ============================================================================
-- Step 4: Update collections table policies to use helper function
-- ============================================================================

DROP POLICY IF EXISTS "Users can view their collections" ON collections;
DROP POLICY IF EXISTS "Users can update collections" ON collections;

CREATE POLICY "Users can view their collections" ON collections
  FOR SELECT USING (
    owner_id = auth.uid()
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

CREATE POLICY "Users can update collections" ON collections
  FOR UPDATE USING (
    owner_id = auth.uid()
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

-- ============================================================================
-- Step 5: Update video_collections policies to use helper function
-- ============================================================================

DROP POLICY IF EXISTS "Collection members can view videos" ON video_collections;
DROP POLICY IF EXISTS "Collection members can add videos" ON video_collections;
DROP POLICY IF EXISTS "Collection members can remove videos" ON video_collections;

CREATE POLICY "Collection members can view videos" ON video_collections
  FOR SELECT USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT get_user_team_ids(auth.uid()))
    )
  );

CREATE POLICY "Collection members can add videos" ON video_collections
  FOR INSERT WITH CHECK (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT get_user_team_ids(auth.uid()))
    )
  );

CREATE POLICY "Collection members can remove videos" ON video_collections
  FOR DELETE USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT get_user_team_ids(auth.uid()))
    )
  );

-- ============================================================================
-- Step 6: Update notifications policies to use helper function
-- ============================================================================

DROP POLICY IF EXISTS "Users can view relevant notifications" ON notifications;
DROP POLICY IF EXISTS "Team owners can create team notifications" ON notifications;

CREATE POLICY "Users can view relevant notifications" ON notifications
  FOR SELECT USING (
    type = 'system'
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

CREATE POLICY "Team owners can create team notifications" ON notifications
  FOR INSERT WITH CHECK (
    type = 'team' AND
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- ============================================================================
-- Done! The helper function bypasses RLS, preventing infinite recursion.
-- ============================================================================
