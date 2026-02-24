-- 077_enhance_resource_tags.sql
-- Add source/confidence to resource_tags, migrate media_tags data, drop media_tags.

BEGIN;

-- ============================================================================
-- 1. Enhance resource_tags with new columns
-- ============================================================================
ALTER TABLE resource_tags ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'user';
ALTER TABLE resource_tags ADD COLUMN IF NOT EXISTS confidence FLOAT;

-- ============================================================================
-- 2. Migrate media_tags data to resource_tags
--    media_tags has (media_id, tag_id) where media_id FK to parsed_media.id
--    resources.video_id links to parsed_media.id
-- ============================================================================
INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
SELECT r.id, mt.tag_id, mt.source, mt.confidence, mt.created_at
FROM media_tags mt
JOIN resources r ON r.media_id = mt.media_id
ON CONFLICT (resource_id, tag_id) DO NOTHING;

-- ============================================================================
-- 3. Drop media_tags table
-- ============================================================================
DROP TABLE IF EXISTS media_tags;

COMMIT;
