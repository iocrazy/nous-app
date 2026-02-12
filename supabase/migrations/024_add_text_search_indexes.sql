-- Migration: Add text search indexes for faster ILIKE queries
-- Description: Create pg_trgm extension and GIN indexes for text search

-- Enable pg_trgm extension for trigram-based text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Create GIN indexes for text search on douyin_videos
-- These indexes speed up ILIKE '%pattern%' queries significantly
-- Note: removed CONCURRENTLY as it cannot run inside a transaction/pipeline

CREATE INDEX IF NOT EXISTS idx_videos_title_trgm
ON douyin_videos USING gin (video_title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_videos_desc_trgm
ON douyin_videos USING gin (video_desc gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_videos_hashtag_trgm
ON douyin_videos USING gin (video_hashtag_name gin_trgm_ops);

-- Create GIN indexes for text search on video_analysis
CREATE INDEX IF NOT EXISTS idx_analysis_visual_desc_trgm
ON video_analysis USING gin (visual_description gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_analysis_detected_text_trgm
ON video_analysis USING gin (detected_text gin_trgm_ops);

-- Add composite index for user_id + created_at for faster user-specific queries
CREATE INDEX IF NOT EXISTS idx_videos_user_created
ON douyin_videos (user_id, created_at DESC);

-- Comment on the indexes
COMMENT ON INDEX idx_videos_title_trgm IS 'GIN index for fast ILIKE text search on video titles';
COMMENT ON INDEX idx_videos_desc_trgm IS 'GIN index for fast ILIKE text search on video descriptions';
COMMENT ON INDEX idx_videos_hashtag_trgm IS 'GIN index for fast ILIKE text search on hashtags';
COMMENT ON INDEX idx_analysis_visual_desc_trgm IS 'GIN index for fast ILIKE text search on AI visual descriptions';
COMMENT ON INDEX idx_analysis_detected_text_trgm IS 'GIN index for fast ILIKE text search on AI detected text';
