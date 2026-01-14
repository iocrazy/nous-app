-- Migration: Add AI content fields to douyin_videos table
-- Description: Store Extract/Rewrite/Analyze generated content

-- Add AI generated content fields
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS ai_extract_text TEXT,
ADD COLUMN IF NOT EXISTS ai_rewrite_text TEXT,
ADD COLUMN IF NOT EXISTS ai_analyze_text TEXT,
ADD COLUMN IF NOT EXISTS ai_generated_at TIMESTAMPTZ;

-- Add comment for documentation
COMMENT ON COLUMN douyin_videos.ai_extract_text IS 'AI extracted summary from video content';
COMMENT ON COLUMN douyin_videos.ai_rewrite_text IS 'AI rewritten video description';
COMMENT ON COLUMN douyin_videos.ai_analyze_text IS 'AI content analysis result';
COMMENT ON COLUMN douyin_videos.ai_generated_at IS 'Timestamp when AI content was last generated';
