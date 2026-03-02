-- Expand team_members role constraint to support new roles
ALTER TABLE team_members DROP CONSTRAINT IF EXISTS team_members_role_check;
ALTER TABLE team_members ADD CONSTRAINT team_members_role_check
  CHECK (role IN ('owner', 'admin', 'editor', 'reviewer', 'viewer'));

-- Migrate existing 'member' rows to 'viewer' (lowest privilege, safe default)
UPDATE team_members SET role = 'viewer' WHERE role = 'member';
