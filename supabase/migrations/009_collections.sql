-- 009_collections.sql
-- Video collections (private and shared)

CREATE TABLE collections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  owner_id UUID REFERENCES auth.users(id) NOT NULL,
  team_id UUID REFERENCES teams(id) ON DELETE SET NULL,  -- NULL = private
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;

-- View: own collections OR team collections
CREATE POLICY "Users can view their collections" ON collections
  FOR SELECT USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- Create: any user can create
CREATE POLICY "Users can create collections" ON collections
  FOR INSERT WITH CHECK (owner_id = auth.uid());

-- Update: owner only for private, team members for shared
CREATE POLICY "Users can update collections" ON collections
  FOR UPDATE USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- Delete: owner only
CREATE POLICY "Owners can delete collections" ON collections
  FOR DELETE USING (owner_id = auth.uid());
