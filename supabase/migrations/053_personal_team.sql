-- 053_personal_team.sql
-- Add is_personal flag to teams table so each user gets
-- an auto-created personal workspace backed by a real team record.

-- ============================================================================
-- Part 1: Add is_personal column
-- ============================================================================

ALTER TABLE teams ADD COLUMN IF NOT EXISTS is_personal BOOLEAN NOT NULL DEFAULT false;

-- ============================================================================
-- Part 2: Update handle_new_user() to auto-create personal team on signup
-- ============================================================================

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
  uname TEXT;
BEGIN
  uname := COALESCE(
    NEW.raw_user_meta_data->>'username',
    split_part(NEW.email, '@', 1)
  );

  -- Create user profile
  INSERT INTO user_profiles (id, username, role)
  VALUES (NEW.id, uname, 'user')
  ON CONFLICT (id) DO NOTHING;

  -- Auto-create personal workspace team
  -- (teams.id defaults to generate_snowflake_id(),
  --  add_owner_as_member trigger handles team_members)
  INSERT INTO teams (name, owner_id, invite_code, is_personal)
  VALUES (
    uname || '''s Workspace',
    NEW.id,
    generate_invite_code(),
    true
  );

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- ============================================================================
-- Part 3: Mark any existing owner-only teams as personal (best-effort cleanup)
-- ============================================================================

-- If a team has exactly one member who is the owner, and it was created
-- by our _auto_create_personal_team backend helper, mark it personal.
UPDATE teams t
SET is_personal = true
WHERE NOT EXISTS (
  SELECT 1 FROM team_members tm
  WHERE tm.team_id = t.id AND tm.user_id <> t.owner_id
)
AND t.is_personal = false
AND t.name LIKE '%''s Workspace';
