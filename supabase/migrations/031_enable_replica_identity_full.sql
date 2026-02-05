-- ===========================================
-- Migration: Enable REPLICA IDENTITY FULL for Realtime
-- ===========================================
-- This enables full row data capture for Supabase Realtime.
-- Without this, only primary key is captured, preventing
-- proper filtering by user_id in realtime subscriptions.

-- Enable REPLICA IDENTITY FULL for douyin_videos
ALTER TABLE douyin_videos REPLICA IDENTITY FULL;

-- Enable for other realtime-enabled tables
ALTER TABLE video_tags REPLICA IDENTITY FULL;
ALTER TABLE tags REPLICA IDENTITY FULL;
ALTER TABLE video_collections REPLICA IDENTITY FULL;
ALTER TABLE collections REPLICA IDENTITY FULL;
ALTER TABLE notifications REPLICA IDENTITY FULL;
ALTER TABLE user_notifications REPLICA IDENTITY FULL;
ALTER TABLE team_members REPLICA IDENTITY FULL;
ALTER TABLE user_logs REPLICA IDENTITY FULL;
