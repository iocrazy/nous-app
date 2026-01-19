-- Migration: Create Cleanup Helper Functions
-- Description: PostgreSQL functions for efficient cleanup suggestions using pgvector

-- Function to find duplicate videos using vector similarity (uses pgvector index)
CREATE OR REPLACE FUNCTION find_duplicate_videos(
    p_user_id uuid,
    similarity_threshold float DEFAULT 0.85,
    max_results int DEFAULT 20
)
RETURNS TABLE (
    video_id bigint,
    video_title text,
    cover_url text,
    author varchar(255),  -- Match actual column type
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
        -- Get all user's videos with embeddings
        SELECT dv.id, va.content_embedding
        FROM douyin_videos dv
        JOIN video_analysis va ON va.video_id = dv.id
        WHERE dv.user_id = p_user_id
        AND va.content_embedding IS NOT NULL
        AND dv.keep_forever = false
    ),
    similarity_pairs AS (
        -- Find similar pairs using vector similarity
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
        dv.video_title,
        dv.cover_url,
        dv.author,
        dv.storage_size,
        dv.created_at,
        dv.last_viewed_at,
        dv.view_count,
        sp.vid1 as similar_to,
        sp.sim_score as similarity_score
    FROM similarity_pairs sp
    JOIN douyin_videos dv ON dv.id = sp.vid2
    ORDER BY sp.vid2, sp.sim_score DESC
    LIMIT max_results;
END;
$$;

COMMENT ON FUNCTION find_duplicate_videos IS 'Find potential duplicate videos using pgvector similarity search. Much faster than Python-based comparison.';

-- Function to get cleanup suggestions in a single query
-- Uses SQL language to avoid PL/pgSQL variable name conflicts
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
    author varchar(255),  -- Match actual column type
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
        -- Never viewed videos (downloaded > N days ago, view_count = 0)
        SELECT
            dv.id,
            dv.video_title,
            dv.cover_url,
            dv.author,
            dv.storage_size,
            dv.created_at,
            dv.last_viewed_at,
            dv.view_count,
            'never_viewed'::text as reason,
            'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed' as reason_detail,
            1 as priority
        FROM douyin_videos dv
        WHERE dv.user_id = p_user_id
        AND dv.keep_forever = false
        AND dv.view_count = 0
        AND dv.created_at < now() - (p_never_viewed_days || ' days')::interval

        UNION ALL

        -- Old unused videos (not viewed in N days, view_count > 0)
        SELECT
            dv.id,
            dv.video_title,
            dv.cover_url,
            dv.author,
            dv.storage_size,
            dv.created_at,
            dv.last_viewed_at,
            dv.view_count,
            'old_unused'::text as reason,
            'Not viewed in over ' || p_old_unused_days || ' days' as reason_detail,
            2 as priority
        FROM douyin_videos dv
        WHERE dv.user_id = p_user_id
        AND dv.keep_forever = false
        AND dv.view_count > 0
        AND dv.last_viewed_at < now() - (p_old_unused_days || ' days')::interval

        UNION ALL

        -- Large files (top 10% by size, excluding already selected)
        SELECT
            dv.id,
            dv.video_title,
            dv.cover_url,
            dv.author,
            dv.storage_size,
            dv.created_at,
            dv.last_viewed_at,
            dv.view_count,
            'large_file'::text as reason,
            'Large file: ' || pg_size_pretty(dv.storage_size) as reason_detail,
            3 as priority
        FROM douyin_videos dv
        WHERE dv.user_id = p_user_id
        AND dv.keep_forever = false
        AND dv.storage_size IS NOT NULL
        AND dv.storage_size > (
            SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
            FROM douyin_videos sub
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

-- Function to get cleanup stats efficiently
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
        COALESCE(SUM(dv.storage_size), 0)::bigint as total_storage_bytes,
        COUNT(*) FILTER (WHERE dv.view_count = 0)::bigint as videos_never_viewed,
        COUNT(*) FILTER (WHERE dv.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
        COUNT(*) FILTER (WHERE dv.keep_forever = true)::bigint as videos_marked_keep,
        COALESCE(SUM(dv.storage_size) FILTER (
            WHERE dv.keep_forever = false
            AND (dv.view_count = 0 OR dv.last_viewed_at < cutoff_30)
        ), 0)::bigint as reclaimable_bytes
    FROM douyin_videos dv
    WHERE dv.user_id = p_user_id;
END;
$$;

COMMENT ON FUNCTION get_cleanup_stats IS 'Get cleanup statistics in a single efficient query.';

-- Combined function to get both suggestions and stats in one call for minimal latency
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
            dv.id as video_id,
            dv.video_title,
            dv.cover_url,
            dv.author,
            dv.storage_size,
            dv.created_at,
            dv.last_viewed_at,
            dv.view_count,
            CASE
                WHEN dv.view_count = 0 AND dv.created_at < now() - (p_never_viewed_days || ' days')::interval THEN 'never_viewed'
                WHEN dv.view_count > 0 AND dv.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 'old_unused'
                ELSE 'large_file'
            END as reason,
            CASE
                WHEN dv.view_count = 0 AND dv.created_at < now() - (p_never_viewed_days || ' days')::interval
                    THEN 'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed'
                WHEN dv.view_count > 0 AND dv.last_viewed_at < now() - (p_old_unused_days || ' days')::interval
                    THEN 'Not viewed in over ' || p_old_unused_days || ' days'
                ELSE 'Large file: ' || pg_size_pretty(dv.storage_size)
            END as reason_detail,
            CASE
                WHEN dv.view_count = 0 THEN 1
                WHEN dv.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 2
                ELSE 3
            END as priority
        FROM douyin_videos dv
        WHERE dv.user_id = p_user_id
        AND dv.keep_forever = false
        AND (
            (dv.view_count = 0 AND dv.created_at < now() - (p_never_viewed_days || ' days')::interval)
            OR (dv.view_count > 0 AND dv.last_viewed_at < now() - (p_old_unused_days || ' days')::interval)
            OR (dv.storage_size IS NOT NULL AND dv.storage_size > (
                SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
                FROM douyin_videos sub
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
        FROM douyin_videos
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

COMMENT ON FUNCTION get_cleanup_data IS 'Combined function to get suggestions, stats, and categories in a single call for optimal performance.';
