-- ============================================================================
-- Migration 036: Add summary_text to videos_with_tags view
-- ============================================================================
-- Updates the videos_with_tags view to LEFT JOIN video_summaries
-- and include the summary_text field for display in frontend.
-- ============================================================================

CREATE OR REPLACE VIEW videos_with_tags AS
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

-- Re-grant access to authenticated users
GRANT SELECT ON videos_with_tags TO authenticated;
