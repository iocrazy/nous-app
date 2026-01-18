-- Add fields to douyin_videos for cleanup suggestions
ALTER TABLE douyin_videos
    ADD COLUMN IF NOT EXISTS view_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_viewed_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS storage_size BIGINT,
    ADD COLUMN IF NOT EXISTS keep_forever BOOLEAN DEFAULT false;

-- Index for cleanup queries
CREATE INDEX IF NOT EXISTS idx_videos_view_count ON douyin_videos(view_count);
CREATE INDEX IF NOT EXISTS idx_videos_last_viewed ON douyin_videos(last_viewed_at);
CREATE INDEX IF NOT EXISTS idx_videos_storage_size ON douyin_videos(storage_size);

-- Function to update view stats from access logs (called periodically)
CREATE OR REPLACE FUNCTION update_video_view_stats()
RETURNS void AS $$
BEGIN
    UPDATE douyin_videos v
    SET
        view_count = stats.cnt,
        last_viewed_at = stats.last_view
    FROM (
        SELECT
            video_id,
            COUNT(*) as cnt,
            MAX(created_at) as last_view
        FROM video_access_logs
        WHERE action IN ('view', 'play')
        GROUP BY video_id
    ) stats
    WHERE v.id = stats.video_id;
END;
$$ LANGUAGE plpgsql;
