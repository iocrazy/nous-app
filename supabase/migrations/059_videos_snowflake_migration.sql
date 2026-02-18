-- 059_videos_snowflake_migration.sql
-- Migrate videos.id from UUID to BIGINT Snowflake ID
-- Uses existing display_id (snowflake) values as the new id.
-- Depends on: 050_snowflake_id_infrastructure.sql (generate_snowflake_id function)
--             055_video_display_id.sql (display_id column already populated)

-- ============================================================================
-- SECTION 0: Drop RLS policies on affected tables
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'videos', 'video_tags', 'video_analysis',
        'video_access_logs', 'video_transcripts', 'video_summaries',
        'video_collections'
      )
  ) LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 1: Remove affected tables from realtime publication
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT schemaname, tablename
    FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename IN (
        'videos', 'video_transcripts', 'video_summaries'
      )
  ) LOOP
    EXECUTE format('ALTER PUBLICATION supabase_realtime DROP TABLE %I.%I', r.schemaname, r.tablename);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 2: Drop dependent views
-- ============================================================================

DROP VIEW IF EXISTS videos_with_tags CASCADE;
DROP VIEW IF EXISTS video_statistics CASCADE;
DROP VIEW IF EXISTS daily_statistics CASCADE;
DROP VIEW IF EXISTS author_statistics CASCADE;

-- ============================================================================
-- SECTION 3: Drop dependent functions (those with uuid return types or video refs)
-- ============================================================================

DROP FUNCTION IF EXISTS match_videos_by_embedding(vector(1536), float, int) CASCADE;
DROP FUNCTION IF EXISTS find_duplicate_videos(uuid, float, int) CASCADE;
DROP FUNCTION IF EXISTS get_cleanup_suggestions(uuid, int, int, int) CASCADE;
DROP FUNCTION IF EXISTS get_cleanup_stats(uuid) CASCADE;
DROP FUNCTION IF EXISTS get_cleanup_data(uuid, int, int, int) CASCADE;
DROP FUNCTION IF EXISTS update_video_view_stats() CASCADE;
DROP FUNCTION IF EXISTS get_user_tag_counts(uuid, int) CASCADE;
DROP FUNCTION IF EXISTS get_dashboard_stats(uuid) CASCADE;

-- ============================================================================
-- SECTION 4: Drop ALL FK constraints referencing videos
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT con.conname, con.conrelid::regclass::text AS child_table
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.confrelid
    WHERE con.contype = 'f'
      AND cls.relname = 'videos'
      AND cls.relnamespace = 'public'::regnamespace
  ) LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.child_table, r.conname);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 5: Drop primary keys on videos + dependent composite PKs
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT con.conname, con.conrelid::regclass::text AS tbl
    FROM pg_constraint con
    WHERE con.contype = 'p'
      AND con.conrelid::regclass::text IN (
        'videos', 'video_tags', 'video_analysis', 'video_collections'
      )
  ) LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.tbl, r.conname);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 6: Drop unique constraints on videos (display_id, platform_source)
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT con.conname, con.conrelid::regclass::text AS tbl
    FROM pg_constraint con
    WHERE con.contype = 'u'
      AND con.conrelid::regclass::text = 'videos'
  ) LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.tbl, r.conname);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 7: Drop indexes on video_id columns
-- ============================================================================

DROP INDEX IF EXISTS idx_video_tags_video;
DROP INDEX IF EXISTS idx_video_tags_tag;
DROP INDEX IF EXISTS idx_access_logs_video;
DROP INDEX IF EXISTS idx_video_transcripts_video_id;
DROP INDEX IF EXISTS idx_video_summaries_video_id;
DROP INDEX IF EXISTS idx_resources_video_id;
DROP INDEX IF EXISTS idx_project_files_video;

-- ============================================================================
-- SECTION 8: Create temp mapping table UUID → BIGINT (display_id)
-- ============================================================================

CREATE TEMP TABLE video_id_mapping AS
SELECT id AS old_id, display_id AS new_id FROM videos;

CREATE UNIQUE INDEX ON video_id_mapping(old_id);
CREATE UNIQUE INDEX ON video_id_mapping(new_id);

-- ============================================================================
-- SECTION 9: Convert FK columns from UUID to BIGINT
-- Using add/update/drop/rename pattern (same as 051)
-- ============================================================================

-- video_tags.video_id (NOT NULL)
ALTER TABLE video_tags ADD COLUMN video_id_new BIGINT;
UPDATE video_tags vt SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = vt.video_id;
ALTER TABLE video_tags DROP COLUMN video_id;
ALTER TABLE video_tags RENAME COLUMN video_id_new TO video_id;
ALTER TABLE video_tags ALTER COLUMN video_id SET NOT NULL;

-- video_analysis.video_id (NOT NULL)
ALTER TABLE video_analysis ADD COLUMN video_id_new BIGINT;
UPDATE video_analysis va SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = va.video_id;
ALTER TABLE video_analysis DROP COLUMN video_id;
ALTER TABLE video_analysis RENAME COLUMN video_id_new TO video_id;
ALTER TABLE video_analysis ALTER COLUMN video_id SET NOT NULL;

-- video_access_logs.video_id (NOT NULL)
ALTER TABLE video_access_logs ADD COLUMN video_id_new BIGINT;
UPDATE video_access_logs val SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = val.video_id;
ALTER TABLE video_access_logs DROP COLUMN video_id;
ALTER TABLE video_access_logs RENAME COLUMN video_id_new TO video_id;
ALTER TABLE video_access_logs ALTER COLUMN video_id SET NOT NULL;

-- video_transcripts.video_id (NOT NULL)
ALTER TABLE video_transcripts ADD COLUMN video_id_new BIGINT;
UPDATE video_transcripts vtr SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = vtr.video_id;
ALTER TABLE video_transcripts DROP COLUMN video_id;
ALTER TABLE video_transcripts RENAME COLUMN video_id_new TO video_id;
ALTER TABLE video_transcripts ALTER COLUMN video_id SET NOT NULL;

-- video_summaries.video_id (NOT NULL)
ALTER TABLE video_summaries ADD COLUMN video_id_new BIGINT;
UPDATE video_summaries vs SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = vs.video_id;
ALTER TABLE video_summaries DROP COLUMN video_id;
ALTER TABLE video_summaries RENAME COLUMN video_id_new TO video_id;
ALTER TABLE video_summaries ALTER COLUMN video_id SET NOT NULL;

-- video_collections.video_id (NOT NULL)
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'video_collections'
  ) THEN
    ALTER TABLE video_collections ADD COLUMN video_id_new BIGINT;
    UPDATE video_collections vc SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = vc.video_id;
    ALTER TABLE video_collections DROP COLUMN video_id;
    ALTER TABLE video_collections RENAME COLUMN video_id_new TO video_id;
    ALTER TABLE video_collections ALTER COLUMN video_id SET NOT NULL;
  END IF;
END $$;

-- resources.video_id (NULLABLE)
ALTER TABLE resources ADD COLUMN video_id_new BIGINT;
UPDATE resources r SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = r.video_id;
ALTER TABLE resources DROP COLUMN video_id;
ALTER TABLE resources RENAME COLUMN video_id_new TO video_id;

-- project_files.video_id (NULLABLE)
ALTER TABLE project_files ADD COLUMN video_id_new BIGINT;
UPDATE project_files pf SET video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = pf.video_id;
ALTER TABLE project_files DROP COLUMN video_id;
ALTER TABLE project_files RENAME COLUMN video_id_new TO video_id;

-- smart_collections.cached_video_ids (UUID[] → BIGINT[], invalidate cache)
ALTER TABLE smart_collections ADD COLUMN cached_video_ids_new BIGINT[] DEFAULT '{}';
ALTER TABLE smart_collections DROP COLUMN IF EXISTS cached_video_ids;
ALTER TABLE smart_collections RENAME COLUMN cached_video_ids_new TO cached_video_ids;

-- search_logs.top_result_video_id (NULLABLE)
ALTER TABLE search_logs ADD COLUMN top_result_video_id_new BIGINT;
UPDATE search_logs sl SET top_result_video_id_new = m.new_id FROM video_id_mapping m WHERE m.old_id = sl.top_result_video_id;
ALTER TABLE search_logs DROP COLUMN top_result_video_id;
ALTER TABLE search_logs RENAME COLUMN top_result_video_id_new TO top_result_video_id;

-- ============================================================================
-- SECTION 10: Swap videos PK from UUID to BIGINT
-- display_id already has snowflake values; drop UUID id, rename display_id → id
-- ============================================================================

ALTER TABLE videos DROP COLUMN id;
ALTER TABLE videos RENAME COLUMN display_id TO id;
ALTER TABLE videos ALTER COLUMN id SET DEFAULT generate_snowflake_id();
ALTER TABLE videos ADD PRIMARY KEY (id);

-- ============================================================================
-- SECTION 11: Recreate composite primary keys
-- ============================================================================

ALTER TABLE video_tags ADD PRIMARY KEY (video_id, tag_id);
ALTER TABLE video_analysis ADD PRIMARY KEY (video_id);

DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'video_collections'
  ) THEN
    ALTER TABLE video_collections ADD PRIMARY KEY (video_id, collection_id);
  END IF;
END $$;

-- ============================================================================
-- SECTION 12: Recreate FK constraints
-- ============================================================================

ALTER TABLE video_tags
  ADD CONSTRAINT video_tags_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;

ALTER TABLE video_analysis
  ADD CONSTRAINT video_analysis_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;

ALTER TABLE video_access_logs
  ADD CONSTRAINT video_access_logs_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;

ALTER TABLE video_transcripts
  ADD CONSTRAINT video_transcripts_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;

ALTER TABLE video_summaries
  ADD CONSTRAINT video_summaries_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;

DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'video_collections'
  ) THEN
    ALTER TABLE video_collections
      ADD CONSTRAINT video_collections_video_id_fkey
      FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;
  END IF;
END $$;

ALTER TABLE resources
  ADD CONSTRAINT resources_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE SET NULL;

ALTER TABLE project_files
  ADD CONSTRAINT project_files_video_id_fkey
  FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE SET NULL;

-- ============================================================================
-- SECTION 13: Recreate unique constraints on videos
-- ============================================================================

ALTER TABLE videos ADD CONSTRAINT videos_platform_source_unique
  UNIQUE (platform_id, source_platform);

-- ============================================================================
-- SECTION 14: Recreate indexes
-- ============================================================================

CREATE INDEX idx_video_tags_video ON video_tags(video_id);
CREATE INDEX idx_video_tags_tag ON video_tags(tag_id);
CREATE INDEX idx_access_logs_video ON video_access_logs(video_id);
CREATE INDEX idx_video_transcripts_video_id ON video_transcripts(video_id);
CREATE INDEX idx_video_summaries_video_id ON video_summaries(video_id);
CREATE INDEX idx_resources_video_id ON resources(video_id) WHERE video_id IS NOT NULL;
CREATE INDEX idx_project_files_video ON project_files(video_id) WHERE video_id IS NOT NULL;

-- Drop temp mapping table
DROP TABLE IF EXISTS video_id_mapping;

-- ============================================================================
-- SECTION 15: Recreate views
-- ============================================================================

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

CREATE VIEW video_statistics WITH (security_invoker = true) AS
SELECT
    COUNT(*) as total_videos,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as downloaded,
    COUNT(*) FILTER (WHERE video_download_status = 'pending') as pending,
    COUNT(*) FILTER (WHERE video_download_status = 'downloading') as downloading,
    COUNT(*) FILTER (WHERE video_download_status = 'failed') as failed,
    COUNT(*) FILTER (WHERE video_download_status = 'skipped') as skipped,
    COUNT(*) FILTER (WHERE cover_download_status = 'completed') as covers_downloaded,
    COUNT(*) FILTER (WHERE media_type = 'video') as standard_videos,
    COUNT(*) FILTER (WHERE media_type = 'carousel') as image_collections,
    COUNT(*) FILTER (WHERE media_type = 'image_text') as image_texts,
    COUNT(*) FILTER (WHERE media_type = 'special') as special_videos,
    COUNT(DISTINCT author) as unique_authors,
    SUM(like_count) as total_likes,
    SUM(comment_count) as total_comments,
    SUM(share_count) as total_shares
FROM videos;

CREATE VIEW daily_statistics AS
SELECT
    DATE(created_at) as date,
    COUNT(*) as videos_added,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as videos_downloaded
FROM videos
GROUP BY DATE(created_at)
ORDER BY date DESC;

CREATE VIEW author_statistics WITH (security_invoker = true) AS
SELECT
    author,
    COUNT(*) as video_count,
    SUM(like_count) as total_likes,
    SUM(comment_count) as total_comments,
    AVG(like_count) as avg_likes
FROM videos
WHERE author IS NOT NULL
GROUP BY author
ORDER BY video_count DESC;

-- ============================================================================
-- SECTION 16: Recreate functions (with BIGINT video_id return types)
-- ============================================================================

CREATE OR REPLACE FUNCTION update_video_view_stats()
RETURNS void AS $$
BEGIN
    UPDATE videos v
    SET
        view_count = stats.cnt,
        last_viewed_at = stats.last_view
    FROM (
        SELECT
            video_id,
            COUNT(*) as cnt,
            MAX(created_at) as last_view
        FROM video_access_logs
        WHERE action IN ('view', 'play')
        GROUP BY video_id
    ) stats
    WHERE v.id = stats.video_id;
END;
$$ LANGUAGE plpgsql;

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
        va.video_id,
        v.platform_id,
        v.title,
        v.description,
        v.cover_url,
        v.author,
        COALESCE(v.view_count, 0)::bigint as view_count,
        v.created_at,
        (1 - (va.content_embedding <=> query_embedding))::float as similarity
    FROM video_analysis va
    JOIN videos v ON v.id = va.video_id
    WHERE va.content_embedding IS NOT NULL
    AND (1 - (va.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY va.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

COMMENT ON FUNCTION match_videos_by_embedding IS 'Search videos by semantic similarity using embeddings.';

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
    WITH user_videos AS (
        SELECT v.id, va.content_embedding
        FROM videos v
        JOIN video_analysis va ON va.video_id = v.id
        WHERE v.user_id = p_user_id
        AND va.content_embedding IS NOT NULL
        AND v.keep_forever = false
    ),
    similarity_pairs AS (
        SELECT
            uv1.id as vid1,
            uv2.id as vid2,
            (1 - (uv1.content_embedding <=> uv2.content_embedding))::float as sim_score
        FROM user_videos uv1
        JOIN user_videos uv2 ON uv2.id > uv1.id
        WHERE (1 - (uv1.content_embedding <=> uv2.content_embedding)) >= similarity_threshold
    )
    SELECT DISTINCT ON (sp.vid2)
        sp.vid2 as video_id,
        v.title as video_title,
        v.cover_url,
        v.author,
        v.storage_size,
        v.created_at,
        v.last_viewed_at,
        v.view_count,
        sp.vid1 as similar_to,
        sp.sim_score as similarity_score
    FROM similarity_pairs sp
    JOIN videos v ON v.id = sp.vid2
    ORDER BY sp.vid2, sp.sim_score DESC
    LIMIT max_results;
END;
$$;

COMMENT ON FUNCTION find_duplicate_videos IS 'Find potential duplicate videos using pgvector similarity search.';

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
            v.id,
            v.title as video_title,
            v.cover_url,
            v.author,
            v.storage_size,
            v.created_at,
            v.last_viewed_at,
            v.view_count,
            'never_viewed'::text as reason,
            'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed' as reason_detail,
            1 as priority
        FROM videos v
        WHERE v.user_id = p_user_id
        AND v.keep_forever = false
        AND v.view_count = 0
        AND v.created_at < now() - (p_never_viewed_days || ' days')::interval

        UNION ALL

        SELECT
            v.id,
            v.title as video_title,
            v.cover_url,
            v.author,
            v.storage_size,
            v.created_at,
            v.last_viewed_at,
            v.view_count,
            'old_unused'::text as reason,
            'Not viewed in over ' || p_old_unused_days || ' days' as reason_detail,
            2 as priority
        FROM videos v
        WHERE v.user_id = p_user_id
        AND v.keep_forever = false
        AND v.view_count > 0
        AND v.last_viewed_at < now() - (p_old_unused_days || ' days')::interval

        UNION ALL

        SELECT
            v.id,
            v.title as video_title,
            v.cover_url,
            v.author,
            v.storage_size,
            v.created_at,
            v.last_viewed_at,
            v.view_count,
            'large_file'::text as reason,
            'Large file: ' || pg_size_pretty(v.storage_size) as reason_detail,
            3 as priority
        FROM videos v
        WHERE v.user_id = p_user_id
        AND v.keep_forever = false
        AND v.storage_size IS NOT NULL
        AND v.storage_size > (
            SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
            FROM videos sub
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

COMMENT ON FUNCTION get_cleanup_suggestions IS 'Get cleanup suggestions (never viewed, old unused, large files).';

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
        COALESCE(SUM(v.storage_size), 0)::bigint as total_storage_bytes,
        COUNT(*) FILTER (WHERE v.view_count = 0)::bigint as videos_never_viewed,
        COUNT(*) FILTER (WHERE v.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
        COUNT(*) FILTER (WHERE v.keep_forever = true)::bigint as videos_marked_keep,
        COALESCE(SUM(v.storage_size) FILTER (
            WHERE v.keep_forever = false
            AND (v.view_count = 0 OR v.last_viewed_at < cutoff_30)
        ), 0)::bigint as reclaimable_bytes
    FROM videos v
    WHERE v.user_id = p_user_id;
END;
$$;

COMMENT ON FUNCTION get_cleanup_stats IS 'Get cleanup statistics.';

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
            v.id as video_id,
            v.title as video_title,
            v.cover_url,
            v.author,
            v.storage_size,
            v.created_at,
            v.last_viewed_at,
            v.view_count,
            CASE
                WHEN v.view_count = 0 AND v.created_at < now() - (p_never_viewed_days || ' days')::interval THEN 'never_viewed'
                WHEN v.view_count > 0 AND v.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 'old_unused'
                ELSE 'large_file'
            END as reason,
            CASE
                WHEN v.view_count = 0 AND v.created_at < now() - (p_never_viewed_days || ' days')::interval
                    THEN 'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed'
                WHEN v.view_count > 0 AND v.last_viewed_at < now() - (p_old_unused_days || ' days')::interval
                    THEN 'Not viewed in over ' || p_old_unused_days || ' days'
                ELSE 'Large file: ' || pg_size_pretty(v.storage_size)
            END as reason_detail,
            CASE
                WHEN v.view_count = 0 THEN 1
                WHEN v.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 2
                ELSE 3
            END as priority
        FROM videos v
        WHERE v.user_id = p_user_id
        AND v.keep_forever = false
        AND (
            (v.view_count = 0 AND v.created_at < now() - (p_never_viewed_days || ' days')::interval)
            OR (v.view_count > 0 AND v.last_viewed_at < now() - (p_old_unused_days || ' days')::interval)
            OR (v.storage_size IS NOT NULL AND v.storage_size > (
                SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
                FROM videos sub
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
        FROM videos
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

COMMENT ON FUNCTION get_cleanup_data IS 'Combined function to get suggestions, stats, and categories.';

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
    JOIN videos v ON vt.video_id = v.id
    WHERE v.user_id = p_user_id
    GROUP BY t.id, t.name, t.color, t.icon, t.type
    ORDER BY count DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

GRANT EXECUTE ON FUNCTION get_user_tag_counts(UUID, INT) TO authenticated;

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
    FROM videos WHERE user_id = p_user_id
  ),
  media AS (
    SELECT jsonb_build_array(jsonb_build_object(
      'video', COUNT(*) FILTER (WHERE media_type IN ('video','special','short','live_clip'))::int,
      'images', COUNT(*) FILTER (WHERE media_type IN ('carousel','image_text'))::int,
      'audio', COUNT(*) FILTER (WHERE need_download_music = true)::int
    )) FROM videos WHERE user_id = p_user_id
  ),
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

-- ============================================================================
-- SECTION 17: Recreate RLS policies on videos
-- ============================================================================

CREATE POLICY "Authenticated users can view all videos" ON videos
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "Authenticated users can insert videos" ON videos
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "Users can update own videos or admin" ON videos
    FOR UPDATE USING (
        auth.uid() = user_id OR
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

CREATE POLICY "Admin can delete videos" ON videos
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- ============================================================================
-- SECTION 18: Recreate RLS policies on dependent tables
-- ============================================================================

-- video_tags
CREATE POLICY "View tags on owned videos" ON video_tags
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_tags.video_id AND videos.user_id = auth.uid())
    );
CREATE POLICY "Add tags to owned videos" ON video_tags
    FOR INSERT WITH CHECK (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_tags.video_id AND videos.user_id = auth.uid())
    );
CREATE POLICY "Remove tags from owned videos" ON video_tags
    FOR DELETE USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_tags.video_id AND videos.user_id = auth.uid())
    );

-- video_analysis
CREATE POLICY "View analysis of owned videos" ON video_analysis
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_analysis.video_id AND videos.user_id = auth.uid())
    );
CREATE POLICY "Manage analysis of owned videos" ON video_analysis
    FOR ALL USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_analysis.video_id AND videos.user_id = auth.uid())
    );

-- video_transcripts
CREATE POLICY "View transcripts of owned videos" ON video_transcripts
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_transcripts.video_id AND videos.user_id = auth.uid())
    );
CREATE POLICY "Manage transcripts of owned videos" ON video_transcripts
    FOR ALL USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_transcripts.video_id AND videos.user_id = auth.uid())
    );

-- video_summaries
CREATE POLICY "View summaries of owned videos" ON video_summaries
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_summaries.video_id AND videos.user_id = auth.uid())
    );
CREATE POLICY "Manage summaries of owned videos" ON video_summaries
    FOR ALL USING (
        EXISTS (SELECT 1 FROM videos WHERE videos.id = video_summaries.video_id AND videos.user_id = auth.uid())
    );

-- video_collections (conditional)
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'video_collections'
  ) THEN
    EXECUTE 'CREATE POLICY "View video collections" ON video_collections FOR SELECT USING (auth.role() = ''authenticated'')';
    EXECUTE 'CREATE POLICY "Manage video collections" ON video_collections FOR ALL USING (auth.role() = ''authenticated'')';
  END IF;
END $$;

-- ============================================================================
-- SECTION 19: Re-enable Realtime
-- ============================================================================

ALTER TABLE videos REPLICA IDENTITY FULL;
ALTER PUBLICATION supabase_realtime ADD TABLE videos;
ALTER PUBLICATION supabase_realtime ADD TABLE video_transcripts;
ALTER PUBLICATION supabase_realtime ADD TABLE video_summaries;

-- ============================================================================
-- SECTION 20: Update comments
-- ============================================================================

COMMENT ON TABLE videos IS 'Main media table. PK is BIGINT Snowflake ID (migrated from UUID in 059).';
COMMENT ON COLUMN videos.id IS 'Snowflake BIGINT primary key (was display_id, originally UUID)';
