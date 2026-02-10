-- Migration: Create RPC function for dashboard statistics
-- Replaces expensive frontend JS computation with a single SQL call

CREATE OR REPLACE FUNCTION get_dashboard_stats(p_user_id UUID)
RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$
DECLARE result JSONB;
BEGIN
  WITH
  -- Basic counts
  basic AS (
    SELECT
      COUNT(*)::int AS total,
      COUNT(*) FILTER (WHERE video_download_status = 'completed')::int AS completed,
      COUNT(*) FILTER (WHERE video_download_status = 'pending')::int AS pending,
      COUNT(*) FILTER (WHERE video_download_status = 'failed')::int AS failed,
      COALESCE(SUM(datasize_bytes), 0)::bigint AS total_storage_bytes,
      COUNT(DISTINCT author)::int AS unique_authors
    FROM videos WHERE user_id = p_user_id
  ),
  -- Media distribution
  media AS (
    SELECT jsonb_build_array(jsonb_build_object(
      'video', COUNT(*) FILTER (WHERE media_type IN ('video','special','short','live_clip'))::int,
      'images', COUNT(*) FILTER (WHERE media_type IN ('carousel','image_text'))::int,
      'audio', COUNT(*) FILTER (WHERE need_download_music = true)::int
    )) FROM videos WHERE user_id = p_user_id
  ),
  -- Weekly activity (last 7 days)
  weekly AS (
    SELECT jsonb_agg(row_to_json(w)::jsonb ORDER BY w.day) FROM (
      SELECT
        d::date AS day,
        TRIM(TO_CHAR(d, 'Dy')) AS name,
        COUNT(v.id)::int AS downloads
      FROM generate_series(
        CURRENT_DATE - INTERVAL '6 days', CURRENT_DATE, '1 day'
      ) d
      LEFT JOIN videos v ON DATE(v.created_at) = d::date AND v.user_id = p_user_id
      GROUP BY d
    ) w
  ),
  -- Top tags
  top_tags AS (
    SELECT jsonb_agg(row_to_json(t)::jsonb) FROM (
      SELECT t.name, COUNT(vt.video_id)::int AS count
      FROM tags t
      JOIN video_tags vt ON t.id = vt.tag_id
      JOIN videos v ON vt.video_id = v.id
      WHERE v.user_id = p_user_id
      GROUP BY t.name ORDER BY count DESC LIMIT 10
    ) t
  )
  SELECT jsonb_build_object(
    'total', b.total,
    'completed', b.completed,
    'pending', b.pending,
    'failed', b.failed,
    'total_storage_bytes', b.total_storage_bytes,
    'unique_authors', b.unique_authors,
    'media_distribution', (SELECT * FROM media),
    'weekly_activity', (SELECT * FROM weekly),
    'top_tags', (SELECT * FROM top_tags)
  ) INTO result FROM basic b;

  RETURN result;
END; $$;

GRANT EXECUTE ON FUNCTION get_dashboard_stats(UUID) TO authenticated;
