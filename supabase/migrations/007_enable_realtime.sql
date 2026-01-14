-- 为 douyin_videos 表启用 Realtime
-- 这允许前端实时订阅数据库变化

-- 将 douyin_videos 表添加到 realtime publication
ALTER PUBLICATION supabase_realtime ADD TABLE douyin_videos;

-- 同时为 user_logs 表启用 Realtime（可选，用于实时日志更新）
ALTER PUBLICATION supabase_realtime ADD TABLE user_logs;

-- 添加注释
COMMENT ON PUBLICATION supabase_realtime IS 'Supabase Realtime publication for douyin_videos and user_logs tables';
