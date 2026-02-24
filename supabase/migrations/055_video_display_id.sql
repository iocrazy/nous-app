-- 055_video_display_id.sql
-- Add Snowflake display_id to videos for URL-friendly video identification.
-- videos.id (UUID) remains the internal PK; display_id is for external URLs.

-- ============================================================================
-- Part 1: Add display_id column
-- ============================================================================

ALTER TABLE videos
ADD COLUMN IF NOT EXISTS display_id BIGINT UNIQUE DEFAULT generate_snowflake_id();

-- ============================================================================
-- Part 2: Backfill existing rows
-- ============================================================================

UPDATE videos
SET display_id = generate_snowflake_id()
WHERE display_id IS NULL;

-- ============================================================================
-- Part 3: Add NOT NULL constraint after backfill
-- ============================================================================

ALTER TABLE videos
ALTER COLUMN display_id SET NOT NULL;

-- ============================================================================
-- Part 4: Rebuild videos_with_tags view to include display_id
-- ============================================================================
-- PostgreSQL expands SELECT * at view creation time, so new columns
-- on the underlying table are not visible until the view is recreated.

DROP VIEW IF EXISTS videos_with_tags;
CREATE VIEW videos_with_tags AS
SELECT
    v.*,
    COALESCE(
        (SELECT json_agg(t.name ORDER BY t.name)
         FROM video_tags vt
         JOIN tags t ON vt.tag_id = t.id
         WHERE vt.video_id = v.id),
        '[]'::json
    ) as tags,
    vs.summary_text
FROM videos v
LEFT JOIN LATERAL (
    SELECT summary_text
    FROM video_summaries
    WHERE video_summaries.video_id = v.id
    ORDER BY created_at DESC
    LIMIT 1
) vs ON true;

GRANT SELECT ON videos_with_tags TO authenticated;
