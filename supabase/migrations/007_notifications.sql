-- 007_notifications.sql
-- System and team notifications

CREATE TABLE notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type VARCHAR(20) NOT NULL CHECK (type IN ('system', 'team')),
  title VARCHAR(200) NOT NULL,
  content TEXT,
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,  -- NULL for system
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE user_notifications (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  notification_id UUID REFERENCES notifications(id) ON DELETE CASCADE,
  read_at TIMESTAMPTZ,  -- NULL = unread
  PRIMARY KEY (user_id, notification_id)
);

-- RLS for notifications
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

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

CREATE TRIGGER notifications_team_trigger
  AFTER INSERT ON notifications
  FOR EACH ROW
  EXECUTE FUNCTION notify_team_members();

-- Index for faster queries
CREATE INDEX idx_user_notifications_unread ON user_notifications(user_id) WHERE read_at IS NULL;
CREATE INDEX idx_notifications_team ON notifications(team_id) WHERE team_id IS NOT NULL;
CREATE INDEX idx_notifications_created ON notifications(created_at DESC);
