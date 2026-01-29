-- Create view for videos with tags array
-- This view simplifies querying videos with their associated tags

CREATE OR REPLACE VIEW videos_with_tags AS
SELECT
    dv.*,
    COALESCE(
        (SELECT json_agg(t.name ORDER BY t.name)
         FROM video_tags vt
         JOIN tags t ON vt.tag_id = t.id
         WHERE vt.video_id = dv.id),
        '[]'::json
    ) as tags
FROM douyin_videos dv;

-- Grant access to authenticated users
GRANT SELECT ON videos_with_tags TO authenticated;
