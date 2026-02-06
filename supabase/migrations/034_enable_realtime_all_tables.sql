-- Migration: Enable Realtime for all applicable tables
-- User selected "D) 全部都要" - enable Realtime for all features

-- Step 1: Set REPLICA IDENTITY FULL for tables that need Realtime but don't have it yet
ALTER TABLE teams REPLICA IDENTITY FULL;
ALTER TABLE smart_collections REPLICA IDENTITY FULL;
ALTER TABLE user_profiles REPLICA IDENTITY FULL;
ALTER TABLE team_invites REPLICA IDENTITY FULL;

-- Step 2: Add all tables to supabase_realtime publication
-- Tables already in publication: douyin_videos, collections, tags, video_collections, video_tags
-- Tables with REPLICA IDENTITY FULL but not in publication:
ALTER PUBLICATION supabase_realtime ADD TABLE notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE user_notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE team_members;
ALTER PUBLICATION supabase_realtime ADD TABLE user_logs;

-- Tables newly configured with REPLICA IDENTITY FULL:
ALTER PUBLICATION supabase_realtime ADD TABLE teams;
ALTER PUBLICATION supabase_realtime ADD TABLE smart_collections;
ALTER PUBLICATION supabase_realtime ADD TABLE user_profiles;
ALTER PUBLICATION supabase_realtime ADD TABLE team_invites;

-- Verify the changes
SELECT schemaname, tablename
FROM pg_publication_tables
WHERE pubname = 'supabase_realtime'
ORDER BY tablename;
