-- 004_team_members.sql
-- Team membership tracking

CREATE TABLE team_members (
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  role VARCHAR(20) DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  joined_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team_id, user_id)
);

-- RLS
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Members can view team members" ON team_members
  FOR SELECT USING (
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

CREATE POLICY "Owners can add members" ON team_members
  FOR INSERT WITH CHECK (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
    OR user_id = auth.uid()  -- Users can add themselves via invite
  );

CREATE POLICY "Owners can remove members" ON team_members
  FOR DELETE USING (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
    OR user_id = auth.uid()  -- Users can leave
  );

-- Auto-add owner as member when team is created
CREATE OR REPLACE FUNCTION add_owner_as_member()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO team_members (team_id, user_id, role)
  VALUES (NEW.id, NEW.owner_id, 'owner');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER teams_add_owner_trigger
  AFTER INSERT ON teams
  FOR EACH ROW
  EXECUTE FUNCTION add_owner_as_member();
