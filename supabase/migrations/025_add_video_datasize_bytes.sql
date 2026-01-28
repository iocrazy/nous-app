-- Migration: Add video_datasize_bytes column for accurate storage calculation
-- This column stores the raw byte size as BIGINT for proper aggregation

-- Add the column
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS video_datasize_bytes BIGINT DEFAULT 0;

-- Create index for efficient aggregation queries
CREATE INDEX IF NOT EXISTS idx_douyin_videos_datasize_bytes
ON douyin_videos(user_id, video_datasize_bytes);

-- Backfill existing data by parsing video_datasize string
-- Format examples: "3.15 MB", "512 KB", "1.2 GB", "15400000" (raw bytes)
UPDATE douyin_videos
SET video_datasize_bytes = CASE
    -- Handle raw numeric values (legacy format)
    WHEN video_datasize ~ '^\d+$' THEN video_datasize::BIGINT
    -- Handle formatted sizes with units
    WHEN video_datasize ILIKE '%GB%' THEN
        (REGEXP_REPLACE(video_datasize, '[^0-9.]', '', 'g')::DECIMAL * 1024 * 1024 * 1024)::BIGINT
    WHEN video_datasize ILIKE '%MB%' THEN
        (REGEXP_REPLACE(video_datasize, '[^0-9.]', '', 'g')::DECIMAL * 1024 * 1024)::BIGINT
    WHEN video_datasize ILIKE '%KB%' THEN
        (REGEXP_REPLACE(video_datasize, '[^0-9.]', '', 'g')::DECIMAL * 1024)::BIGINT
    WHEN video_datasize ILIKE '%B%' THEN
        (REGEXP_REPLACE(video_datasize, '[^0-9.]', '', 'g')::DECIMAL)::BIGINT
    ELSE 0
END
WHERE video_datasize IS NOT NULL AND video_datasize_bytes = 0;

-- Comment for documentation
COMMENT ON COLUMN douyin_videos.video_datasize_bytes IS 'Raw video file size in bytes for aggregation queries';
