-- 022_create_team_invites_table.sql
-- Team invite links with expiration and usage limits

-- Create team_invites table
CREATE TABLE IF NOT EXISTS team_invites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  code VARCHAR(20) UNIQUE NOT NULL,
  created_by UUID REFERENCES auth.users(id),
  expires_at TIMESTAMPTZ,
  max_uses INTEGER,
  use_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE team_invites ENABLE ROW LEVEL SECURITY;

-- Index for quick code lookup
CREATE INDEX IF NOT EXISTS idx_team_invites_code ON team_invites(code);
CREATE INDEX IF NOT EXISTS idx_team_invites_team ON team_invites(team_id);

-- RLS Policies

-- Team members can view their team's invites
CREATE POLICY "Team members can view invites"
ON team_invites FOR SELECT
USING (
  team_id IN (
    SELECT team_id FROM team_members WHERE user_id = auth.uid()
  )
);

-- Team owners/admins can create invites
CREATE POLICY "Team owners can create invites"
ON team_invites FOR INSERT
WITH CHECK (
  team_id IN (
    SELECT id FROM teams WHERE owner_id = auth.uid()
  )
);

-- Team owners can delete invites
CREATE POLICY "Team owners can delete invites"
ON team_invites FOR DELETE
USING (
  team_id IN (
    SELECT id FROM teams WHERE owner_id = auth.uid()
  )
);

-- Team owners can update invites (for use_count increment)
CREATE POLICY "Team owners can update invites"
ON team_invites FOR UPDATE
USING (
  team_id IN (
    SELECT id FROM teams WHERE owner_id = auth.uid()
  )
);

-- Function to generate unique invite code (different from team invite_code)
CREATE OR REPLACE FUNCTION generate_team_invite_code()
RETURNS TEXT AS $$
DECLARE
  chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  result TEXT := '';
  i INT;
BEGIN
  FOR i IN 1..12 LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::int, 1);
  END LOOP;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- Auto-generate invite code on insert
CREATE OR REPLACE FUNCTION set_team_invite_code()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.code IS NULL THEN
    NEW.code := generate_team_invite_code();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS team_invites_code_trigger ON team_invites;
CREATE TRIGGER team_invites_code_trigger
  BEFORE INSERT ON team_invites
  FOR EACH ROW
  EXECUTE FUNCTION set_team_invite_code();

-- Add admin role to team_members role check (update existing constraint)
ALTER TABLE team_members DROP CONSTRAINT IF EXISTS team_members_role_check;
ALTER TABLE team_members ADD CONSTRAINT team_members_role_check
  CHECK (role IN ('owner', 'admin', 'member'));
