-- Drop deprecated video_categories column
-- This field has been replaced by the video_tags table (many-to-many relationship)

-- Remove the column
ALTER TABLE douyin_videos DROP COLUMN IF EXISTS video_categories;

-- Add comment explaining the migration
COMMENT ON TABLE video_tags IS 'Replaces the deprecated video_categories column in douyin_videos. Use this table for tag associations.';
