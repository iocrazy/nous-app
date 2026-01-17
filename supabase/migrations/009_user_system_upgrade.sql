-- 009_user_system_upgrade.sql
-- Comprehensive user system upgrade: teams, team_members, notifications, collections team support
-- Execute this in Supabase SQL Editor

-- ============================================================================
-- Part 1: Team Members Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS team_members (
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  role VARCHAR(20) DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  joined_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (team_id, user_id)
);

ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;

-- RLS Policies for team_members
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

DROP TRIGGER IF EXISTS teams_add_owner_trigger ON teams;
CREATE TRIGGER teams_add_owner_trigger
  AFTER INSERT ON teams
  FOR EACH ROW
  EXECUTE FUNCTION add_owner_as_member();

-- ============================================================================
-- Part 2: Teams Table RLS Policies (if not exists)
-- ============================================================================

-- Generate unique invite code function
CREATE OR REPLACE FUNCTION generate_invite_code()
RETURNS TEXT AS $$
DECLARE
  chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  result TEXT := '';
  i INT;
BEGIN
  FOR i IN 1..8 LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::int, 1);
  END LOOP;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- Auto-generate invite code on insert
CREATE OR REPLACE FUNCTION set_invite_code()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.invite_code IS NULL THEN
    NEW.invite_code := generate_invite_code();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS teams_invite_code_trigger ON teams;
CREATE TRIGGER teams_invite_code_trigger
  BEFORE INSERT ON teams
  FOR EACH ROW
  EXECUTE FUNCTION set_invite_code();

-- Drop existing policies if any (to avoid conflicts)
DROP POLICY IF EXISTS "Users can view teams they belong to" ON teams;
DROP POLICY IF EXISTS "Users can create teams" ON teams;
DROP POLICY IF EXISTS "Owners can update their teams" ON teams;
DROP POLICY IF EXISTS "Owners can delete their teams" ON teams;

-- Create new policies
CREATE POLICY "Users can view teams they belong to" ON teams
  FOR SELECT USING (
    id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    OR owner_id = auth.uid()
  );

CREATE POLICY "Users can create teams" ON teams
  FOR INSERT WITH CHECK (owner_id = auth.uid());

CREATE POLICY "Owners can update their teams" ON teams
  FOR UPDATE USING (owner_id = auth.uid());

CREATE POLICY "Owners can delete their teams" ON teams
  FOR DELETE USING (owner_id = auth.uid());

-- ============================================================================
-- Part 3: Upgrade Collections Table for Team Support
-- ============================================================================

-- Add team_id column if not exists
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'collections' AND column_name = 'team_id'
  ) THEN
    ALTER TABLE collections ADD COLUMN team_id UUID REFERENCES teams(id) ON DELETE SET NULL;
  END IF;
END $$;

-- Add owner_id column if not exists (rename user_id to owner_id for consistency)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'collections' AND column_name = 'owner_id'
  ) THEN
    -- If user_id exists, rename it to owner_id
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'collections' AND column_name = 'user_id'
    ) THEN
      ALTER TABLE collections RENAME COLUMN user_id TO owner_id;
    ELSE
      ALTER TABLE collections ADD COLUMN owner_id UUID REFERENCES auth.users(id);
    END IF;
  END IF;
END $$;

-- Drop existing policies on collections
DROP POLICY IF EXISTS "Users can view their collections" ON collections;
DROP POLICY IF EXISTS "Users can create collections" ON collections;
DROP POLICY IF EXISTS "Users can update collections" ON collections;
DROP POLICY IF EXISTS "Owners can delete collections" ON collections;
DROP POLICY IF EXISTS "Users can view own collections" ON collections;
DROP POLICY IF EXISTS "Users can manage own collections" ON collections;

-- Create new policies supporting both private and team collections
CREATE POLICY "Users can view their collections" ON collections
  FOR SELECT USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

CREATE POLICY "Users can create collections" ON collections
  FOR INSERT WITH CHECK (owner_id = auth.uid());

CREATE POLICY "Users can update collections" ON collections
  FOR UPDATE USING (
    owner_id = auth.uid() OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

CREATE POLICY "Owners can delete collections" ON collections
  FOR DELETE USING (owner_id = auth.uid());

-- ============================================================================
-- Part 4: Upgrade video_collections Table
-- ============================================================================

-- Add added_by column if not exists
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'video_collections' AND column_name = 'added_by'
  ) THEN
    ALTER TABLE video_collections ADD COLUMN added_by UUID REFERENCES auth.users(id);
  END IF;
END $$;

-- Drop existing policies on video_collections
DROP POLICY IF EXISTS "Collection members can view videos" ON video_collections;
DROP POLICY IF EXISTS "Collection members can add videos" ON video_collections;
DROP POLICY IF EXISTS "Collection members can remove videos" ON video_collections;
DROP POLICY IF EXISTS "Users can view collection videos" ON video_collections;
DROP POLICY IF EXISTS "Users can manage collection videos" ON video_collections;

-- Create new policies supporting team collaboration
CREATE POLICY "Collection members can view videos" ON video_collections
  FOR SELECT USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

CREATE POLICY "Collection members can add videos" ON video_collections
  FOR INSERT WITH CHECK (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

CREATE POLICY "Collection members can remove videos" ON video_collections
  FOR DELETE USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

-- ============================================================================
-- Part 5: Notifications System
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type VARCHAR(20) NOT NULL CHECK (type IN ('system', 'team')),
  title VARCHAR(200) NOT NULL,
  content TEXT,
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,  -- NULL for system
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_notifications (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  notification_id UUID REFERENCES notifications(id) ON DELETE CASCADE,
  read_at TIMESTAMPTZ,  -- NULL = unread
  PRIMARY KEY (user_id, notification_id)
);

-- RLS for notifications
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view relevant notifications" ON notifications;
DROP POLICY IF EXISTS "Team owners can create team notifications" ON notifications;

CREATE POLICY "Users can view relevant notifications" ON notifications
  FOR SELECT USING (
    type = 'system' OR
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );

-- Only admins can create system notifications (via service role)
-- Team owners can create team notifications
CREATE POLICY "Team owners can create team notifications" ON notifications
  FOR INSERT WITH CHECK (
    type = 'team' AND
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- RLS for user_notifications
ALTER TABLE user_notifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view their notification status" ON user_notifications;
DROP POLICY IF EXISTS "Users can update their notification status" ON user_notifications;

CREATE POLICY "Users can view their notification status" ON user_notifications
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can update their notification status" ON user_notifications
  FOR ALL USING (user_id = auth.uid());

-- Auto-create user_notification entries for team notifications
CREATE OR REPLACE FUNCTION notify_team_members()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.type = 'team' AND NEW.team_id IS NOT NULL THEN
    INSERT INTO user_notifications (user_id, notification_id)
    SELECT user_id, NEW.id FROM team_members WHERE team_id = NEW.team_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS notifications_team_trigger ON notifications;
CREATE TRIGGER notifications_team_trigger
  AFTER INSERT ON notifications
  FOR EACH ROW
  EXECUTE FUNCTION notify_team_members();

-- Indexes for better performance
CREATE INDEX IF NOT EXISTS idx_user_notifications_unread ON user_notifications(user_id) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_team ON notifications(team_id) WHERE team_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);
CREATE INDEX IF NOT EXISTS idx_collections_team ON collections(team_id) WHERE team_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_collections_owner ON collections(owner_id);

-- ============================================================================
-- Part 6: Enable Realtime for new tables
-- ============================================================================

-- Enable realtime for notifications
ALTER PUBLICATION supabase_realtime ADD TABLE notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE user_notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE team_members;

-- Done!
-- After running this migration, verify by checking:
-- SELECT * FROM team_members LIMIT 1;
-- SELECT * FROM notifications LIMIT 1;
-- SELECT * FROM user_notifications LIMIT 1;
