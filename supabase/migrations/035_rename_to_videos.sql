-- ============================================================================
-- Migration 035: Rename douyin_videos → videos, UUID PK, generalized fields
-- Part of: Multi-source video support (Whisper + yt-dlp + LLM)
-- ============================================================================
-- This migration:
--   1. Drops all dependent views, functions, triggers, policies, indexes
--   2. Renames table douyin_videos → videos
--   3. Changes PK from BIGSERIAL to UUID
--   4. Renames Douyin-specific columns to generic names
--   5. Adds new columns for multi-source support
--   6. Recreates all dependent objects with updated references
--   7. Creates new tables: video_transcripts, video_summaries
-- ============================================================================

BEGIN;

-- ============================================================================
-- PHASE 0: Safety check
-- ============================================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'douyin_videos'
    ) THEN
        RAISE EXCEPTION 'Table douyin_videos does not exist. Migration may have already been applied.';
    END IF;
END $$;

-- ============================================================================
-- PHASE 1: Drop dependent views
-- ============================================================================
DROP VIEW IF EXISTS video_statistics CASCADE;
DROP VIEW IF EXISTS daily_statistics CASCADE;
DROP VIEW IF EXISTS author_statistics CASCADE;
DROP VIEW IF EXISTS videos_with_tags CASCADE;

-- ============================================================================
-- PHASE 2: Drop dependent functions
-- ============================================================================
DROP FUNCTION IF EXISTS match_videos_by_embedding(vector(1536), float, int) CASCADE;
DROP FUNCTION IF EXISTS find_duplicate_videos(uuid, float, int) CASCADE;
DROP FUNCTION IF EXISTS get_cleanup_suggestions(uuid, int, int, int) CASCADE;
DROP FUNCTION IF EXISTS get_cleanup_stats(uuid) CASCADE;
DROP FUNCTION IF EXISTS get_cleanup_data(uuid, int, int, int) CASCADE;
DROP FUNCTION IF EXISTS update_video_view_stats() CASCADE;
DROP FUNCTION IF EXISTS update_daily_statistics() CASCADE;
DROP FUNCTION IF EXISTS get_user_tag_counts(uuid, int) CASCADE;

-- ============================================================================
-- PHASE 3: Drop RLS policies on douyin_videos
-- ============================================================================
DROP POLICY IF EXISTS "认证用户可以查看所有视频" ON douyin_videos;
DROP POLICY IF EXISTS "认证用户可以插入视频" ON douyin_videos;
DROP POLICY IF EXISTS "用户可以更新自己的视频" ON douyin_videos;
DROP POLICY IF EXISTS "管理员可以删除视频" ON douyin_videos;

-- ============================================================================
-- PHASE 4: Drop RLS policies on dependent tables that reference douyin_videos
-- ============================================================================

-- video_tags policies
DROP POLICY IF EXISTS "View tags on owned videos" ON video_tags;
DROP POLICY IF EXISTS "Add tags to owned videos" ON video_tags;
DROP POLICY IF EXISTS "Remove tags from owned videos" ON video_tags;

-- video_analysis policies
DROP POLICY IF EXISTS "View analysis of owned videos" ON video_analysis;
DROP POLICY IF EXISTS "Manage analysis of owned videos" ON video_analysis;

-- ============================================================================
-- PHASE 5: Drop triggers on douyin_videos
-- ============================================================================
DROP TRIGGER IF EXISTS update_douyin_videos_updated_at ON douyin_videos;

-- ============================================================================
-- PHASE 6: Drop indexes on douyin_videos (they will be recreated with new names)
-- ============================================================================
DROP INDEX IF EXISTS idx_douyin_videos_aweme_id;
DROP INDEX IF EXISTS idx_douyin_videos_author;
DROP INDEX IF EXISTS idx_douyin_videos_download_status;
DROP INDEX IF EXISTS idx_douyin_videos_created_at;
DROP INDEX IF EXISTS idx_douyin_videos_user_id;
DROP INDEX IF EXISTS idx_douyin_videos_aweme_type;
DROP INDEX IF EXISTS idx_douyin_videos_author_id;
DROP INDEX IF EXISTS idx_douyin_videos_cover_status;
DROP INDEX IF EXISTS idx_videos_view_count;
DROP INDEX IF EXISTS idx_videos_last_viewed;
DROP INDEX IF EXISTS idx_videos_storage_size;
DROP INDEX IF EXISTS idx_videos_title_trgm;
DROP INDEX IF EXISTS idx_videos_desc_trgm;
DROP INDEX IF EXISTS idx_videos_hashtag_trgm;
DROP INDEX IF EXISTS idx_videos_user_created;
DROP INDEX IF EXISTS idx_douyin_videos_datasize_bytes;

-- ============================================================================
-- PHASE 7: Remove from realtime publication
-- ============================================================================
ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS douyin_videos;

-- ============================================================================
-- PHASE 8: Drop foreign key constraints on dependent tables
-- (So we can change the PK type)
-- ============================================================================

-- video_collections: drop FK and PK
ALTER TABLE video_collections DROP CONSTRAINT IF EXISTS video_collections_pkey;
ALTER TABLE video_collections DROP CONSTRAINT IF EXISTS video_collections_video_id_fkey;

-- video_tags: drop PK and FK
ALTER TABLE video_tags DROP CONSTRAINT IF EXISTS video_tags_pkey;
ALTER TABLE video_tags DROP CONSTRAINT IF EXISTS video_tags_video_id_fkey;

-- video_analysis: drop PK and FK
ALTER TABLE video_analysis DROP CONSTRAINT IF EXISTS video_analysis_pkey;
ALTER TABLE video_analysis DROP CONSTRAINT IF EXISTS video_analysis_video_id_fkey;

-- video_access_logs: drop FK
ALTER TABLE video_access_logs DROP CONSTRAINT IF EXISTS video_access_logs_video_id_fkey;

-- ============================================================================
-- PHASE 9: Rename table douyin_videos → videos
-- ============================================================================
ALTER TABLE douyin_videos RENAME TO videos;

-- ============================================================================
-- PHASE 10: Change primary key from BIGSERIAL to UUID
-- ============================================================================

-- Add new UUID column
ALTER TABLE videos ADD COLUMN new_id UUID DEFAULT gen_random_uuid();

-- Populate UUID for existing rows
UPDATE videos SET new_id = gen_random_uuid() WHERE new_id IS NULL;

-- Create mapping table for FK updates
CREATE TEMP TABLE id_mapping AS
SELECT id AS old_id, new_id FROM videos;

-- Update dependent tables' foreign keys to use new UUID values

-- video_collections
ALTER TABLE video_collections ADD COLUMN new_video_id UUID;
UPDATE video_collections vc
SET new_video_id = m.new_id
FROM id_mapping m
WHERE vc.video_id = m.old_id;

-- video_tags
ALTER TABLE video_tags ADD COLUMN new_video_id UUID;
UPDATE video_tags vt
SET new_video_id = m.new_id
FROM id_mapping m
WHERE vt.video_id = m.old_id;

-- video_analysis
ALTER TABLE video_analysis ADD COLUMN new_video_id UUID;
UPDATE video_analysis va
SET new_video_id = m.new_id
FROM id_mapping m
WHERE va.video_id = m.old_id;

-- video_access_logs
ALTER TABLE video_access_logs ADD COLUMN new_video_id UUID;
UPDATE video_access_logs val
SET new_video_id = m.new_id
FROM id_mapping m
WHERE val.video_id = m.old_id;

-- smart_collections: convert cached_video_ids from BIGINT[] to UUID[]
ALTER TABLE smart_collections ADD COLUMN new_cached_video_ids UUID[] DEFAULT '{}';
-- Note: cached_video_ids will be invalidated; set to empty (caches will rebuild)
ALTER TABLE smart_collections DROP COLUMN IF EXISTS cached_video_ids;
ALTER TABLE smart_collections RENAME COLUMN new_cached_video_ids TO cached_video_ids;

-- search_logs: top_result_video_id
ALTER TABLE search_logs ADD COLUMN new_top_result_video_id UUID;
UPDATE search_logs sl
SET new_top_result_video_id = m.new_id
FROM id_mapping m
WHERE sl.top_result_video_id = m.old_id;
ALTER TABLE search_logs DROP COLUMN top_result_video_id;
ALTER TABLE search_logs RENAME COLUMN new_top_result_video_id TO top_result_video_id;

-- Now swap PK column on videos table
ALTER TABLE videos DROP CONSTRAINT IF EXISTS douyin_videos_pkey;
ALTER TABLE videos DROP COLUMN id;
ALTER TABLE videos RENAME COLUMN new_id TO id;
ALTER TABLE videos ADD PRIMARY KEY (id);

-- Drop old sequence (BIGSERIAL creates a sequence)
DROP SEQUENCE IF EXISTS douyin_videos_id_seq;

-- Swap FK columns on dependent tables

-- video_collections
ALTER TABLE video_collections DROP COLUMN video_id;
ALTER TABLE video_collections RENAME COLUMN new_video_id TO video_id;
ALTER TABLE video_collections ALTER COLUMN video_id SET NOT NULL;

-- video_tags
ALTER TABLE video_tags DROP COLUMN video_id;
ALTER TABLE video_tags RENAME COLUMN new_video_id TO video_id;
ALTER TABLE video_tags ALTER COLUMN video_id SET NOT NULL;

-- video_analysis
ALTER TABLE video_analysis DROP COLUMN video_id;
ALTER TABLE video_analysis RENAME COLUMN new_video_id TO video_id;
ALTER TABLE video_analysis ALTER COLUMN video_id SET NOT NULL;

-- video_access_logs
ALTER TABLE video_access_logs DROP COLUMN video_id;
ALTER TABLE video_access_logs RENAME COLUMN new_video_id TO video_id;
ALTER TABLE video_access_logs ALTER COLUMN video_id SET NOT NULL;

-- Drop temp mapping table
DROP TABLE IF EXISTS id_mapping;

-- ============================================================================
-- PHASE 11: Recreate foreign keys and primary keys on dependent tables
-- ============================================================================

-- video_collections
ALTER TABLE video_collections
    ADD CONSTRAINT video_collections_video_id_fkey
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;
ALTER TABLE video_collections
    ADD PRIMARY KEY (video_id, collection_id);

-- video_tags
ALTER TABLE video_tags
    ADD CONSTRAINT video_tags_video_id_fkey
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;
ALTER TABLE video_tags
    ADD PRIMARY KEY (video_id, tag_id);

-- video_analysis
ALTER TABLE video_analysis
    ADD CONSTRAINT video_analysis_video_id_fkey
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;
ALTER TABLE video_analysis
    ADD PRIMARY KEY (video_id);

-- video_access_logs
ALTER TABLE video_access_logs
    ADD CONSTRAINT video_access_logs_video_id_fkey
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE;

-- ============================================================================
-- PHASE 12: Rename columns on videos table
-- ============================================================================
ALTER TABLE videos RENAME COLUMN aweme_id TO platform_id;
ALTER TABLE videos RENAME COLUMN aweme_type TO media_type;
ALTER TABLE videos RENAME COLUMN video_digg_count TO like_count;
ALTER TABLE videos RENAME COLUMN video_collect_count TO favorite_count;
ALTER TABLE videos RENAME COLUMN video_comment_count TO comment_count;
ALTER TABLE videos RENAME COLUMN video_share_count TO share_count;
ALTER TABLE videos RENAME COLUMN video_original_url TO original_url;
ALTER TABLE videos RENAME COLUMN video_duration TO duration;
ALTER TABLE videos RENAME COLUMN video_resolution TO resolution;
ALTER TABLE videos RENAME COLUMN video_datasize TO datasize;
ALTER TABLE videos RENAME COLUMN video_datasize_bytes TO datasize_bytes;
ALTER TABLE videos RENAME COLUMN video_hashtag_name TO hashtags;
ALTER TABLE videos RENAME COLUMN video_created_time TO published_at;
ALTER TABLE videos RENAME COLUMN video_title TO title;
ALTER TABLE videos RENAME COLUMN video_desc TO description;

-- ============================================================================
-- PHASE 13: Map media_type values (aweme_type numeric → descriptive strings)
-- ============================================================================
UPDATE videos SET media_type = CASE media_type
    WHEN '0' THEN 'video'
    WHEN '2' THEN 'carousel'
    WHEN '68' THEN 'image_text'
    WHEN '4' THEN 'special'
    WHEN '61' THEN 'special'
    WHEN '150' THEN 'special'
    WHEN '157' THEN 'special'
    ELSE COALESCE(media_type, 'video')
END;

-- ============================================================================
-- PHASE 14: Add new columns for multi-source support
-- ============================================================================

-- Source identification
ALTER TABLE videos ADD COLUMN IF NOT EXISTS source_platform VARCHAR(50) NOT NULL DEFAULT 'douyin';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS external_id VARCHAR(255);

-- Populate external_id from platform_id for existing records
UPDATE videos SET external_id = platform_id WHERE external_id IS NULL;

-- Transcription & analysis status
ALTER TABLE videos ADD COLUMN IF NOT EXISTS transcript_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS summary_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS visual_analysis_status VARCHAR(20) DEFAULT 'pending';

-- Automation toggles
ALTER TABLE videos ADD COLUMN IF NOT EXISTS transcript_bool BOOLEAN DEFAULT true;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS summary_bool BOOLEAN DEFAULT true;

-- HLS streaming
ALTER TABLE videos ADD COLUMN IF NOT EXISTS hls_path TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS media_format VARCHAR(10) DEFAULT 'mp4';

-- ============================================================================
-- PHASE 15: Update constraints
-- ============================================================================

-- Drop old UNIQUE on aweme_id (now platform_id)
ALTER TABLE videos DROP CONSTRAINT IF EXISTS douyin_videos_aweme_id_key;

-- Add composite unique constraint
ALTER TABLE videos ADD CONSTRAINT videos_platform_source_unique
    UNIQUE (platform_id, source_platform);

-- ============================================================================
-- PHASE 16: Recreate indexes on videos
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_videos_platform_id ON videos(platform_id);
CREATE INDEX IF NOT EXISTS idx_videos_author ON videos(author);
CREATE INDEX IF NOT EXISTS idx_videos_download_status ON videos(video_download_status);
CREATE INDEX IF NOT EXISTS idx_videos_created_at ON videos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_user_id ON videos(user_id);
CREATE INDEX IF NOT EXISTS idx_videos_media_type ON videos(media_type);
CREATE INDEX IF NOT EXISTS idx_videos_author_id ON videos(author_id);
CREATE INDEX IF NOT EXISTS idx_videos_cover_status ON videos(cover_download_status);
CREATE INDEX IF NOT EXISTS idx_videos_view_count ON videos(view_count);
CREATE INDEX IF NOT EXISTS idx_videos_last_viewed ON videos(last_viewed_at);
CREATE INDEX IF NOT EXISTS idx_videos_storage_size ON videos(storage_size);
CREATE INDEX IF NOT EXISTS idx_videos_user_created ON videos(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_datasize_bytes ON videos(user_id, datasize_bytes);
CREATE INDEX IF NOT EXISTS idx_videos_source_platform ON videos(source_platform);
CREATE INDEX IF NOT EXISTS idx_videos_transcript_status ON videos(transcript_status);
CREATE INDEX IF NOT EXISTS idx_videos_summary_status ON videos(summary_status);

-- Text search indexes (trigram)
CREATE INDEX IF NOT EXISTS idx_videos_title_trgm ON videos USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_videos_desc_trgm ON videos USING gin (description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_videos_hashtag_trgm ON videos USING gin (hashtags gin_trgm_ops);

-- Indexes on dependent tables
CREATE INDEX IF NOT EXISTS idx_video_tags_video ON video_tags(video_id);
CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_access_logs_video ON video_access_logs(video_id);

-- ============================================================================
-- PHASE 17: Recreate trigger on videos
-- ============================================================================
CREATE TRIGGER update_videos_updated_at
    BEFORE UPDATE ON videos
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- PHASE 18: Recreate RLS policies on videos
-- ============================================================================
-- (RLS is already enabled from the old table; just recreate policies)

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
-- PHASE 19: Recreate RLS policies on video_tags (reference videos instead of douyin_videos)
-- ============================================================================

CREATE POLICY "View tags on owned videos" ON video_tags
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_tags.video_id
            AND videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Add tags to owned videos" ON video_tags
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_tags.video_id
            AND videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Remove tags from owned videos" ON video_tags
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_tags.video_id
            AND videos.user_id = auth.uid()
        )
    );

-- ============================================================================
-- PHASE 20: Recreate RLS policies on video_analysis (reference videos)
-- ============================================================================

CREATE POLICY "View analysis of owned videos" ON video_analysis
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_analysis.video_id
            AND videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Manage analysis of owned videos" ON video_analysis
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_analysis.video_id
            AND videos.user_id = auth.uid()
        )
    );

-- ============================================================================
-- PHASE 21: Recreate views
-- ============================================================================

-- Video statistics view
CREATE OR REPLACE VIEW video_statistics WITH (security_invoker = true) AS
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

-- Daily statistics view
CREATE OR REPLACE VIEW daily_statistics AS
SELECT
    DATE(created_at) as date,
    COUNT(*) as videos_added,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as videos_downloaded
FROM videos
GROUP BY DATE(created_at)
ORDER BY date DESC;

-- Author statistics view
CREATE OR REPLACE VIEW author_statistics WITH (security_invoker = true) AS
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

-- Videos with tags view
CREATE OR REPLACE VIEW videos_with_tags AS
SELECT
    v.*,
    COALESCE(
        (SELECT json_agg(t.name ORDER BY t.name)
         FROM video_tags vt
         JOIN tags t ON vt.tag_id = t.id
         WHERE vt.video_id = v.id),
        '[]'::json
    ) as tags
FROM videos v;

GRANT SELECT ON videos_with_tags TO authenticated;

-- ============================================================================
-- PHASE 22: Recreate functions
-- ============================================================================

-- update_daily_statistics
CREATE OR REPLACE FUNCTION update_daily_statistics()
RETURNS VOID AS $$
BEGIN
    -- Note: statistics table was dropped in migration 002.
    -- This function is kept for API compatibility but is a no-op.
    NULL;
END;
$$ LANGUAGE plpgsql;

-- update_video_view_stats
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

-- match_videos_by_embedding
CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    video_id uuid,
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

COMMENT ON FUNCTION match_videos_by_embedding IS 'Search videos by semantic similarity using embeddings. Returns videos with platform_id, metadata, and similarity above threshold.';

-- find_duplicate_videos
CREATE OR REPLACE FUNCTION find_duplicate_videos(
    p_user_id uuid,
    similarity_threshold float DEFAULT 0.85,
    max_results int DEFAULT 20
)
RETURNS TABLE (
    video_id uuid,
    video_title text,
    cover_url text,
    author varchar(255),
    storage_size bigint,
    created_at timestamptz,
    last_viewed_at timestamptz,
    view_count int,
    similar_to uuid,
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

-- get_cleanup_suggestions
CREATE OR REPLACE FUNCTION get_cleanup_suggestions(
    p_user_id uuid,
    p_never_viewed_days int DEFAULT 7,
    p_old_unused_days int DEFAULT 30,
    p_limit int DEFAULT 50
)
RETURNS TABLE (
    video_id uuid,
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

COMMENT ON FUNCTION get_cleanup_suggestions IS 'Get cleanup suggestions (never viewed, old unused, large files) in a single efficient query.';

-- get_cleanup_stats
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

COMMENT ON FUNCTION get_cleanup_stats IS 'Get cleanup statistics in a single efficient query.';

-- get_cleanup_data
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

COMMENT ON FUNCTION get_cleanup_data IS 'Combined function to get suggestions, stats, and categories in a single call.';

-- get_user_tag_counts
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

-- ============================================================================
-- PHASE 23: Re-enable Realtime
-- ============================================================================
ALTER TABLE videos REPLICA IDENTITY FULL;
ALTER PUBLICATION supabase_realtime ADD TABLE videos;

-- ============================================================================
-- PHASE 24: Create new tables for transcription and summarization
-- ============================================================================

-- Video transcripts table
CREATE TABLE IF NOT EXISTS video_transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    language VARCHAR(10),
    full_text TEXT,
    segments JSONB,
    whisper_model VARCHAR(50),
    duration_seconds FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_transcripts_video_id ON video_transcripts(video_id);

-- RLS for video_transcripts
ALTER TABLE video_transcripts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View transcripts of owned videos" ON video_transcripts
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_transcripts.video_id
            AND videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Manage transcripts of owned videos" ON video_transcripts
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_transcripts.video_id
            AND videos.user_id = auth.uid()
        )
    );

-- Video summaries table
CREATE TABLE IF NOT EXISTS video_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    summary_type VARCHAR(20),
    summary_text TEXT,
    key_points JSONB,
    topics JSONB,
    llm_model VARCHAR(50),
    llm_provider VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_video_summaries_video_id ON video_summaries(video_id);

-- RLS for video_summaries
ALTER TABLE video_summaries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View summaries of owned videos" ON video_summaries
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_summaries.video_id
            AND videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Manage summaries of owned videos" ON video_summaries
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM videos
            WHERE videos.id = video_summaries.video_id
            AND videos.user_id = auth.uid()
        )
    );

-- Enable Realtime for new tables
ALTER TABLE video_transcripts REPLICA IDENTITY FULL;
ALTER TABLE video_summaries REPLICA IDENTITY FULL;
ALTER PUBLICATION supabase_realtime ADD TABLE video_transcripts;
ALTER PUBLICATION supabase_realtime ADD TABLE video_summaries;

-- ============================================================================
-- PHASE 25: Add comments for documentation
-- ============================================================================
COMMENT ON TABLE videos IS 'Main media table (renamed from douyin_videos). Supports multiple source platforms.';
COMMENT ON COLUMN videos.platform_id IS 'Unique identifier on the source platform (was aweme_id)';
COMMENT ON COLUMN videos.media_type IS 'Content type: video, carousel, image_text, special, short, live_clip';
COMMENT ON COLUMN videos.source_platform IS 'Source platform: douyin, youtube, bilibili, twitter, other';
COMMENT ON COLUMN videos.source_url IS 'Original URL submitted by user';
COMMENT ON COLUMN videos.external_id IS 'Platform original ID (preserves aweme_id value for Douyin)';
COMMENT ON COLUMN videos.transcript_status IS 'Transcription status: pending, processing, completed, failed, skipped';
COMMENT ON COLUMN videos.summary_status IS 'Summary status: pending, processing, completed, failed, skipped';
COMMENT ON COLUMN videos.visual_analysis_status IS 'Visual analysis status: pending, processing, completed, failed, skipped';
COMMENT ON COLUMN videos.hls_path IS 'HLS playlist path relative to downloads directory';
COMMENT ON COLUMN videos.media_format IS 'Storage format: mp4 (legacy) or hls (new)';
COMMENT ON TABLE video_transcripts IS 'Whisper transcription results with timestamped segments';
COMMENT ON TABLE video_summaries IS 'LLM-generated summaries with key points and topics';

COMMIT;
