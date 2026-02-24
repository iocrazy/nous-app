-- 066_rename_videos_to_parsed_media.sql
-- Full rename: videos ecosystem → parsed_media ecosystem
--
-- Scope:
--   7 tables renamed
--   9 columns renamed (video_id → media_id)
--   2 views renamed
--   ~35 indexes renamed
--   8 PL/pgSQL/SQL functions updated (body references new table/column names)
--   Table comments updated
--
-- RLS policies & Realtime publication auto-follow renames (stored by OID).
-- Function bodies are stored as TEXT → must be explicitly updated.

BEGIN;

-- ═══════════════════════════════════════════════════════════════
-- 1. RENAME TABLES
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE videos RENAME TO parsed_media;
ALTER TABLE video_transcripts RENAME TO media_transcripts;
ALTER TABLE video_summaries RENAME TO media_summaries;
ALTER TABLE video_collections RENAME TO media_collections;
ALTER TABLE video_tags RENAME TO media_tags;
ALTER TABLE video_analysis RENAME TO media_analysis;
ALTER TABLE video_access_logs RENAME TO media_access_logs;

-- ═══════════════════════════════════════════════════════════════
-- 2. RENAME COLUMNS: video_id → media_id (FK columns in related tables)
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE media_transcripts RENAME COLUMN video_id TO media_id;
ALTER TABLE media_summaries RENAME COLUMN video_id TO media_id;
ALTER TABLE media_collections RENAME COLUMN video_id TO media_id;
ALTER TABLE media_tags RENAME COLUMN video_id TO media_id;
ALTER TABLE media_analysis RENAME COLUMN video_id TO media_id;
ALTER TABLE media_access_logs RENAME COLUMN video_id TO media_id;
ALTER TABLE resources RENAME COLUMN video_id TO media_id;
ALTER TABLE project_files RENAME COLUMN video_id TO media_id;
ALTER TABLE unified_tasks RENAME COLUMN video_id TO media_id;

-- ═══════════════════════════════════════════════════════════════
-- 3. RENAME VIEWS
--    View definitions auto-follow table/column renames (stored by OID).
--    Only the view object names need renaming.
-- ═══════════════════════════════════════════════════════════════

ALTER VIEW videos_with_tags RENAME TO parsed_media_with_tags;
ALTER VIEW video_statistics RENAME TO media_statistics;
-- daily_statistics, author_statistics: names don't mention "video" — kept as-is

-- ═══════════════════════════════════════════════════════════════
-- 4. RENAME INDEXES
--    Index functionality auto-follows renames (stored by OID).
--    Names are cosmetic but important for maintenance.
--    Using IF EXISTS for safety (some may have been dropped in prior migrations).
-- ═══════════════════════════════════════════════════════════════

-- 4a. Indexes on parsed_media (was videos) — from migration 035
ALTER INDEX IF EXISTS idx_videos_platform_id RENAME TO idx_parsed_media_platform_id;
ALTER INDEX IF EXISTS idx_videos_author RENAME TO idx_parsed_media_author;
ALTER INDEX IF EXISTS idx_videos_download_status RENAME TO idx_parsed_media_download_status;
ALTER INDEX IF EXISTS idx_videos_created_at RENAME TO idx_parsed_media_created_at;
ALTER INDEX IF EXISTS idx_videos_user_id RENAME TO idx_parsed_media_user_id;
ALTER INDEX IF EXISTS idx_videos_media_type RENAME TO idx_parsed_media_media_type;
ALTER INDEX IF EXISTS idx_videos_author_id RENAME TO idx_parsed_media_author_id;
ALTER INDEX IF EXISTS idx_videos_cover_status RENAME TO idx_parsed_media_cover_status;
ALTER INDEX IF EXISTS idx_videos_view_count RENAME TO idx_parsed_media_view_count;
ALTER INDEX IF EXISTS idx_videos_last_viewed RENAME TO idx_parsed_media_last_viewed;
ALTER INDEX IF EXISTS idx_videos_storage_size RENAME TO idx_parsed_media_storage_size;
ALTER INDEX IF EXISTS idx_videos_user_created RENAME TO idx_parsed_media_user_created;
ALTER INDEX IF EXISTS idx_videos_datasize_bytes RENAME TO idx_parsed_media_datasize_bytes;
ALTER INDEX IF EXISTS idx_videos_source_platform RENAME TO idx_parsed_media_source_platform;
ALTER INDEX IF EXISTS idx_videos_transcript_status RENAME TO idx_parsed_media_transcript_status;
ALTER INDEX IF EXISTS idx_videos_summary_status RENAME TO idx_parsed_media_summary_status;
ALTER INDEX IF EXISTS idx_videos_title_trgm RENAME TO idx_parsed_media_title_trgm;
ALTER INDEX IF EXISTS idx_videos_desc_trgm RENAME TO idx_parsed_media_desc_trgm;
ALTER INDEX IF EXISTS idx_videos_hashtag_trgm RENAME TO idx_parsed_media_hashtag_trgm;

-- 4b. Indexes on media_tags (was video_tags) — from migration 059
ALTER INDEX IF EXISTS idx_video_tags_video RENAME TO idx_media_tags_media;
ALTER INDEX IF EXISTS idx_video_tags_tag RENAME TO idx_media_tags_tag;
ALTER INDEX IF EXISTS idx_video_tags_source RENAME TO idx_media_tags_source;

-- 4c. Indexes on media_analysis (was video_analysis) — from migrations 014, 024
ALTER INDEX IF EXISTS idx_video_analysis_embedding RENAME TO idx_media_analysis_embedding;
ALTER INDEX IF EXISTS idx_video_analysis_level RENAME TO idx_media_analysis_level;
ALTER INDEX IF EXISTS idx_analysis_visual_desc_trgm RENAME TO idx_media_analysis_visual_desc_trgm;
ALTER INDEX IF EXISTS idx_analysis_detected_text_trgm RENAME TO idx_media_analysis_detected_text_trgm;

-- 4d. Indexes on media_access_logs (was video_access_logs) — from migration 016
ALTER INDEX IF EXISTS idx_access_logs_video RENAME TO idx_media_access_logs_media;
ALTER INDEX IF EXISTS idx_access_logs_user RENAME TO idx_media_access_logs_user;
ALTER INDEX IF EXISTS idx_access_logs_created RENAME TO idx_media_access_logs_created;
ALTER INDEX IF EXISTS idx_access_logs_action RENAME TO idx_media_access_logs_action;

-- 4e. Indexes on media_transcripts / media_summaries — from migration 059
ALTER INDEX IF EXISTS idx_video_transcripts_video_id RENAME TO idx_media_transcripts_media_id;
ALTER INDEX IF EXISTS idx_video_summaries_video_id RENAME TO idx_media_summaries_media_id;

-- 4f. Indexes on resources / project_files (column renamed) — from migration 059
ALTER INDEX IF EXISTS idx_resources_video_id RENAME TO idx_resources_media_id;
ALTER INDEX IF EXISTS idx_project_files_video RENAME TO idx_project_files_media;

-- ═══════════════════════════════════════════════════════════════
-- 5. UPDATE PL/pgSQL & SQL FUNCTIONS
--    Function bodies are stored as TEXT and re-parsed at runtime.
--    They MUST be updated to reference new table/column names.
--    Function signatures (names, args, return types) are NOT changed
--    to avoid breaking callers and GRANTs.
-- ═══════════════════════════════════════════════════════════════

-- 5a. update_video_view_stats()
CREATE OR REPLACE FUNCTION update_video_view_stats()
RETURNS void AS $$
BEGIN
    UPDATE parsed_media pm
    SET
        view_count = stats.cnt,
        last_viewed_at = stats.last_view
    FROM (
        SELECT
            media_id,
            COUNT(*) as cnt,
            MAX(created_at) as last_view
        FROM media_access_logs
        WHERE action IN ('view', 'play')
        GROUP BY media_id
    ) stats
    WHERE pm.id = stats.media_id;
END;
$$ LANGUAGE plpgsql;

-- 5b. match_videos_by_embedding()
CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    video_id bigint,
    platform_id text,
    title text,
    description text,
    cover_url text,
    author text,
    view_count bigint,
    created_at timestamptz,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ma.media_id,
        pm.platform_id,
        pm.title,
        pm.description,
        pm.cover_url,
        pm.author,
        COALESCE(pm.view_count, 0)::bigint as view_count,
        pm.created_at,
        (1 - (ma.content_embedding <=> query_embedding))::float as similarity
    FROM media_analysis ma
    JOIN parsed_media pm ON pm.id = ma.media_id
    WHERE ma.content_embedding IS NOT NULL
    AND (1 - (ma.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY ma.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 5c. find_duplicate_videos()
CREATE OR REPLACE FUNCTION find_duplicate_videos(
    p_user_id uuid,
    similarity_threshold float DEFAULT 0.85,
    max_results int DEFAULT 20
)
RETURNS TABLE (
    video_id bigint,
    video_title text,
    cover_url text,
    author varchar(255),
    storage_size bigint,
    created_at timestamptz,
    last_viewed_at timestamptz,
    view_count int,
    similar_to bigint,
    similarity_score float
)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY
    WITH user_media AS (
        SELECT pm.id, ma.content_embedding
        FROM parsed_media pm
        JOIN media_analysis ma ON ma.media_id = pm.id
        WHERE pm.user_id = p_user_id
        AND ma.content_embedding IS NOT NULL
        AND pm.keep_forever = false
    ),
    similarity_pairs AS (
        SELECT
            um1.id as mid1,
            um2.id as mid2,
            (1 - (um1.content_embedding <=> um2.content_embedding))::float as sim_score
        FROM user_media um1
        JOIN user_media um2 ON um2.id > um1.id
        WHERE (1 - (um1.content_embedding <=> um2.content_embedding)) >= similarity_threshold
    )
    SELECT DISTINCT ON (sp.mid2)
        sp.mid2 as video_id,
        pm.title as video_title,
        pm.cover_url,
        pm.author,
        pm.storage_size,
        pm.created_at,
        pm.last_viewed_at,
        pm.view_count,
        sp.mid1 as similar_to,
        sp.sim_score as similarity_score
    FROM similarity_pairs sp
    JOIN parsed_media pm ON pm.id = sp.mid2
    ORDER BY sp.mid2, sp.sim_score DESC
    LIMIT max_results;
END;
$$;

-- 5d. get_cleanup_suggestions()
CREATE OR REPLACE FUNCTION get_cleanup_suggestions(
    p_user_id uuid,
    p_never_viewed_days int DEFAULT 7,
    p_old_unused_days int DEFAULT 30,
    p_limit int DEFAULT 50
)
RETURNS TABLE (
    video_id bigint,
    video_title text,
    cover_url text,
    author varchar(255),
    storage_size bigint,
    created_at timestamptz,
    last_viewed_at timestamptz,
    view_count int,
    reason text,
    reason_detail text
)
LANGUAGE sql
STABLE
AS $$
    WITH suggestions AS (
        SELECT
            pm.id,
            pm.title as video_title,
            pm.cover_url,
            pm.author,
            pm.storage_size,
            pm.created_at,
            pm.last_viewed_at,
            pm.view_count,
            'never_viewed'::text as reason,
            'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed' as reason_detail,
            1 as priority
        FROM parsed_media pm
        WHERE pm.user_id = p_user_id
        AND pm.keep_forever = false
        AND pm.view_count = 0
        AND pm.created_at < now() - (p_never_viewed_days || ' days')::interval

        UNION ALL

        SELECT
            pm.id,
            pm.title as video_title,
            pm.cover_url,
            pm.author,
            pm.storage_size,
            pm.created_at,
            pm.last_viewed_at,
            pm.view_count,
            'old_unused'::text as reason,
            'Not viewed in over ' || p_old_unused_days || ' days' as reason_detail,
            2 as priority
        FROM parsed_media pm
        WHERE pm.user_id = p_user_id
        AND pm.keep_forever = false
        AND pm.view_count > 0
        AND pm.last_viewed_at < now() - (p_old_unused_days || ' days')::interval

        UNION ALL

        SELECT
            pm.id,
            pm.title as video_title,
            pm.cover_url,
            pm.author,
            pm.storage_size,
            pm.created_at,
            pm.last_viewed_at,
            pm.view_count,
            'large_file'::text as reason,
            'Large file: ' || pg_size_pretty(pm.storage_size) as reason_detail,
            3 as priority
        FROM parsed_media pm
        WHERE pm.user_id = p_user_id
        AND pm.keep_forever = false
        AND pm.storage_size IS NOT NULL
        AND pm.storage_size > (
            SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
            FROM parsed_media sub
            WHERE sub.user_id = p_user_id AND sub.storage_size IS NOT NULL
        )
    )
    SELECT DISTINCT ON (s.id)
        s.id,
        s.video_title,
        s.cover_url,
        s.author,
        s.storage_size,
        s.created_at,
        s.last_viewed_at,
        s.view_count,
        s.reason,
        s.reason_detail
    FROM suggestions s
    ORDER BY s.id, s.priority
    LIMIT p_limit;
$$;

-- 5e. get_cleanup_stats()
CREATE OR REPLACE FUNCTION get_cleanup_stats(p_user_id uuid)
RETURNS TABLE (
    total_videos bigint,
    total_storage_bytes bigint,
    videos_never_viewed bigint,
    videos_not_viewed_30_days bigint,
    videos_marked_keep bigint,
    reclaimable_bytes bigint
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    cutoff_30 timestamptz := now() - interval '30 days';
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::bigint as total_videos,
        COALESCE(SUM(pm.storage_size), 0)::bigint as total_storage_bytes,
        COUNT(*) FILTER (WHERE pm.view_count = 0)::bigint as videos_never_viewed,
        COUNT(*) FILTER (WHERE pm.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
        COUNT(*) FILTER (WHERE pm.keep_forever = true)::bigint as videos_marked_keep,
        COALESCE(SUM(pm.storage_size) FILTER (
            WHERE pm.keep_forever = false
            AND (pm.view_count = 0 OR pm.last_viewed_at < cutoff_30)
        ), 0)::bigint as reclaimable_bytes
    FROM parsed_media pm
    WHERE pm.user_id = p_user_id;
END;
$$;

-- 5f. get_cleanup_data()
CREATE OR REPLACE FUNCTION get_cleanup_data(
    p_user_id uuid,
    p_never_viewed_days int DEFAULT 7,
    p_old_unused_days int DEFAULT 30,
    p_limit int DEFAULT 50
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    result jsonb;
    cutoff_30 timestamptz := now() - interval '30 days';
BEGIN
    WITH
    suggestions AS (
        SELECT
            pm.id as video_id,
            pm.title as video_title,
            pm.cover_url,
            pm.author,
            pm.storage_size,
            pm.created_at,
            pm.last_viewed_at,
            pm.view_count,
            CASE
                WHEN pm.view_count = 0 AND pm.created_at < now() - (p_never_viewed_days || ' days')::interval THEN 'never_viewed'
                WHEN pm.view_count > 0 AND pm.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 'old_unused'
                ELSE 'large_file'
            END as reason,
            CASE
                WHEN pm.view_count = 0 AND pm.created_at < now() - (p_never_viewed_days || ' days')::interval
                    THEN 'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed'
                WHEN pm.view_count > 0 AND pm.last_viewed_at < now() - (p_old_unused_days || ' days')::interval
                    THEN 'Not viewed in over ' || p_old_unused_days || ' days'
                ELSE 'Large file: ' || pg_size_pretty(pm.storage_size)
            END as reason_detail,
            CASE
                WHEN pm.view_count = 0 THEN 1
                WHEN pm.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 2
                ELSE 3
            END as priority
        FROM parsed_media pm
        WHERE pm.user_id = p_user_id
        AND pm.keep_forever = false
        AND (
            (pm.view_count = 0 AND pm.created_at < now() - (p_never_viewed_days || ' days')::interval)
            OR (pm.view_count > 0 AND pm.last_viewed_at < now() - (p_old_unused_days || ' days')::interval)
            OR (pm.storage_size IS NOT NULL AND pm.storage_size > (
                SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
                FROM parsed_media sub
                WHERE sub.user_id = p_user_id AND sub.storage_size IS NOT NULL
            ))
        )
        ORDER BY priority, storage_size DESC NULLS LAST
        LIMIT p_limit
    ),
    stats AS (
        SELECT
            COUNT(*)::bigint as total_videos,
            COALESCE(SUM(storage_size), 0)::bigint as total_storage_bytes,
            COUNT(*) FILTER (WHERE view_count = 0)::bigint as videos_never_viewed,
            COUNT(*) FILTER (WHERE last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
            COUNT(*) FILTER (WHERE keep_forever = true)::bigint as videos_marked_keep,
            COALESCE(SUM(storage_size) FILTER (
                WHERE keep_forever = false
                AND (view_count = 0 OR last_viewed_at < cutoff_30)
            ), 0)::bigint as reclaimable_bytes
        FROM parsed_media
        WHERE user_id = p_user_id
    ),
    categories AS (
        SELECT
            COUNT(*) FILTER (WHERE reason = 'never_viewed')::int as never_viewed,
            COUNT(*) FILTER (WHERE reason = 'old_unused')::int as old_unused,
            COUNT(*) FILTER (WHERE reason = 'large_file')::int as large_file
        FROM suggestions
    )
    SELECT jsonb_build_object(
        'suggestions', COALESCE((SELECT jsonb_agg(row_to_json(s.*)) FROM suggestions s), '[]'::jsonb),
        'stats', (SELECT row_to_json(st.*) FROM stats st),
        'categories', (SELECT row_to_json(c.*) FROM categories c)
    ) INTO result;

    RETURN result;
END;
$$;

-- 5g. get_user_tag_counts()
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
        COUNT(mt.media_id) as count
    FROM tags t
    JOIN media_tags mt ON t.id = mt.tag_id
    JOIN parsed_media pm ON mt.media_id = pm.id
    WHERE pm.user_id = p_user_id
    GROUP BY t.id, t.name, t.color, t.icon, t.type
    ORDER BY count DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5h. get_dashboard_stats()
CREATE OR REPLACE FUNCTION get_dashboard_stats(p_user_id UUID)
RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$
DECLARE result JSONB;
BEGIN
  WITH
  basic AS (
    SELECT
      COUNT(*)::int AS total,
      COUNT(*) FILTER (WHERE video_download_status = 'completed')::int AS completed,
      COUNT(*) FILTER (WHERE video_download_status = 'pending')::int AS pending,
      COUNT(*) FILTER (WHERE video_download_status = 'failed')::int AS failed,
      COALESCE(SUM(datasize_bytes), 0)::bigint AS total_storage_bytes,
      COUNT(DISTINCT author)::int AS unique_authors
    FROM parsed_media WHERE user_id = p_user_id
  ),
  media AS (
    SELECT jsonb_build_array(jsonb_build_object(
      'video', COUNT(*) FILTER (WHERE media_type IN ('video','special','short','live_clip'))::int,
      'images', COUNT(*) FILTER (WHERE media_type IN ('carousel','image_text'))::int,
      'audio', COUNT(*) FILTER (WHERE need_download_music = true)::int
    )) FROM parsed_media WHERE user_id = p_user_id
  ),
  weekly AS (
    SELECT jsonb_agg(row_to_json(w)::jsonb ORDER BY w.day) FROM (
      SELECT
        d::date AS day,
        TRIM(TO_CHAR(d, 'Dy')) AS name,
        COUNT(pm.id)::int AS downloads
      FROM generate_series(
        CURRENT_DATE - INTERVAL '6 days', CURRENT_DATE, '1 day'
      ) d
      LEFT JOIN parsed_media pm ON DATE(pm.created_at) = d::date AND pm.user_id = p_user_id
      GROUP BY d
    ) w
  ),
  top_tags AS (
    SELECT jsonb_agg(row_to_json(t)::jsonb) FROM (
      SELECT t.name, COUNT(mt.media_id)::int AS count
      FROM tags t
      JOIN media_tags mt ON t.id = mt.tag_id
      JOIN parsed_media pm ON mt.media_id = pm.id
      WHERE pm.user_id = p_user_id
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

-- ═══════════════════════════════════════════════════════════════
-- 6. UPDATE TABLE COMMENTS
-- ═══════════════════════════════════════════════════════════════

COMMENT ON TABLE parsed_media IS 'Main media table (renamed from videos). PK is BIGINT Snowflake ID.';
COMMENT ON TABLE media_transcripts IS 'AI-generated transcripts for parsed media.';
COMMENT ON TABLE media_summaries IS 'AI-generated summaries for parsed media.';
COMMENT ON TABLE media_tags IS 'Tag assignments for parsed media.';
COMMENT ON TABLE media_analysis IS 'AI visual analysis and embeddings for parsed media.';
COMMENT ON TABLE media_access_logs IS 'User access logs for parsed media.';
COMMENT ON TABLE media_collections IS 'Many-to-many: media items in collections.';

-- ═══════════════════════════════════════════════════════════════
-- 7. NOTES
--
-- RLS policies: Auto-follow table/column renames (stored by OID).
--   Policy NAMES still mention "videos" — cosmetic only, no functional impact.
--
-- Realtime publication: Entries auto-follow table renames (stored by OID).
--   parsed_media, media_transcripts, media_summaries remain in publication.
--
-- REPLICA IDENTITY: Set on table, auto-follows rename.
--
-- GRANTs: Auto-follow renames (stored by OID).
--
-- Triggers: video_analysis_updated trigger auto-follows table rename.
--   Trigger function update_video_analysis_timestamp() doesn't reference
--   table names in body (just does NEW.updated_at = now()).
--
-- Foreign keys: Auto-follow table/column renames (stored by OID).
-- ═══════════════════════════════════════════════════════════════

COMMIT;
