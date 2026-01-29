-- Create RPC function for getting user tag counts
-- This function returns tags with their usage count for a specific user

CREATE OR REPLACE FUNCTION get_user_tag_counts(p_user_id UUID, p_limit INT DEFAULT 10)
RETURNS TABLE (
    id UUID,
    name VARCHAR,
    color VARCHAR,
    icon VARCHAR,
    type VARCHAR,
    count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        t.id,
        t.name,
        t.color,
        t.icon,
        t.type,
        COUNT(vt.video_id) as count
    FROM tags t
    JOIN video_tags vt ON t.id = vt.tag_id
    JOIN douyin_videos dv ON vt.video_id = dv.id
    WHERE dv.user_id = p_user_id
    GROUP BY t.id, t.name, t.color, t.icon, t.type
    ORDER BY count DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION get_user_tag_counts(UUID, INT) TO authenticated;
