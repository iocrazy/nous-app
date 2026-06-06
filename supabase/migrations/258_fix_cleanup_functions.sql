-- 258_fix_cleanup_functions.sql
--
-- Repair the 4 cleanup RPCs after the ownership-model migrations broke them.
-- Last defined in mig 066, all four referenced columns/tables that no longer
-- exist, so every call raised and cleanup_service.py silently fell through to
-- its (schema-valid) fallback/legacy paths on every request — spamming the
-- error log and losing the RPC fast-path:
--
--   * mig 083 dropped parsed_media.user_id (per-user ownership moved to
--     resources.creator_id). The mig 066 bodies filtered `pm.user_id =
--     p_user_id` → `column pm.user_id does not exist`.
--   * mig 086 dropped parsed_media.cover_url (cover URLs now live in the
--     cover_urls text[] array). The mig 066 bodies SELECTed `pm.cover_url`.
--   * mig 076 renamed media_analysis → resource_analysis AND its key column
--     media_id → resource_id (now keyed on resources.id, not parsed_media.id).
--     find_duplicate_videos JOINed the gone `media_analysis` on `media_id`.
--
-- Resource-centric redefinition (matches the working fallbacks in
-- cleanup_service.py): "a user's media" = the parsed_media rows referenced by
-- the resources they own (resources.creator_id = p_user_id, is_trashed = false,
-- media_id not null). All per-item stats (storage_size / view_count /
-- last_viewed_at / keep_forever / title / author / cover_urls / created_at)
-- still live on parsed_media; we just reach them through the owned resources.
-- find_duplicate_videos reaches resource_analysis.content_embedding via
-- resource_analysis.resource_id = resources.id.
--
-- RETURN-SHAPE ALIGNMENT (forces DROP-first): the mig 066 functions returned
-- columns named video_id / video_title / cover_url, but cleanup_service.py
-- consumes media_id / title / cover_urls. That mismatch was masked because the
-- RPC always raised and the service always used the fallback. We rename the
-- return columns to what the service actually reads (and cover_url text →
-- cover_urls text[]) so the restored fast-path produces the SAME result the
-- fallback does. Postgres forbids changing a RETURNS TABLE column set/type via
-- CREATE OR REPLACE, so each function is DROP'd first with its exact live arg
-- signature (mirrors mig 256 / 035 / 059). No _rollback.sql.
--
-- SECURITY DEFINER + SET search_path = public, pg_catalog applied per mig 127
-- (mig 127 had locked search_path on 3 of these; find_duplicate_videos was not
-- in that batch — we lock it here too).

-- ─────────────────────────────────────────────────────────────────────────
-- 1. find_duplicate_videos(p_user_id uuid, similarity_threshold float, max_results int)
--    pgvector self-similarity over the OWNED resources' analysis embeddings.
-- ─────────────────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS find_duplicate_videos(uuid, double precision, integer) CASCADE;

CREATE OR REPLACE FUNCTION find_duplicate_videos(
    p_user_id uuid,
    similarity_threshold float DEFAULT 0.85,
    max_results int DEFAULT 20
)
RETURNS TABLE (
    media_id bigint,
    title text,
    cover_urls text[],
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
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
BEGIN
    RETURN QUERY
    WITH user_media AS (
        SELECT pm.id, ra.content_embedding
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        JOIN resource_analysis ra ON ra.resource_id = r.id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
        AND ra.content_embedding IS NOT NULL
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
        sp.mid2 as media_id,
        pm.title,
        pm.cover_urls,
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

-- ─────────────────────────────────────────────────────────────────────────
-- 2. get_cleanup_suggestions(p_user_id uuid, p_never_viewed_days int,
--    p_old_unused_days int, p_limit int)
--    Mirrors _get_suggestions_fallback: never_viewed / old_unused / large_file
--    over the user's owned, non-trashed media (parsed_media via resources).
-- ─────────────────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS get_cleanup_suggestions(uuid, integer, integer, integer) CASCADE;

CREATE OR REPLACE FUNCTION get_cleanup_suggestions(
    p_user_id uuid,
    p_never_viewed_days int DEFAULT 7,
    p_old_unused_days int DEFAULT 30,
    p_limit int DEFAULT 50
)
RETURNS TABLE (
    media_id bigint,
    title text,
    cover_urls text[],
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
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    -- The user's owned, non-trashed media (distinct media_ids).
    WITH owned AS (
        SELECT DISTINCT pm.id,
               pm.title,
               pm.cover_urls,
               pm.author,
               pm.storage_size,
               pm.created_at,
               pm.last_viewed_at,
               pm.view_count,
               pm.keep_forever
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
    ),
    suggestions AS (
        SELECT
            o.id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            'never_viewed'::text as reason,
            'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed' as reason_detail,
            1 as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND o.view_count = 0
        AND o.created_at < now() - (p_never_viewed_days || ' days')::interval

        UNION ALL

        SELECT
            o.id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            'old_unused'::text as reason,
            'Not viewed in over ' || p_old_unused_days || ' days' as reason_detail,
            2 as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND o.view_count > 0
        AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval

        UNION ALL

        SELECT
            o.id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            'large_file'::text as reason,
            'Large file: ' || pg_size_pretty(o.storage_size) as reason_detail,
            3 as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND o.storage_size IS NOT NULL
        AND o.storage_size > (
            SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
            FROM owned sub
            WHERE sub.storage_size IS NOT NULL
        )
    )
    SELECT DISTINCT ON (s.id)
        s.id as media_id,
        s.title,
        s.cover_urls,
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

-- ─────────────────────────────────────────────────────────────────────────
-- 3. get_cleanup_stats(p_user_id uuid)
--    Mirrors _get_cleanup_stats_fallback over the user's owned media.
-- ─────────────────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS get_cleanup_stats(uuid) CASCADE;

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
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    cutoff_30 timestamptz := now() - interval '30 days';
BEGIN
    RETURN QUERY
    WITH owned AS (
        SELECT DISTINCT pm.id,
               pm.storage_size,
               pm.view_count,
               pm.last_viewed_at,
               pm.keep_forever
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
    )
    SELECT
        COUNT(*)::bigint as total_videos,
        COALESCE(SUM(o.storage_size), 0)::bigint as total_storage_bytes,
        COUNT(*) FILTER (WHERE o.view_count = 0)::bigint as videos_never_viewed,
        COUNT(*) FILTER (WHERE o.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
        COUNT(*) FILTER (WHERE o.keep_forever = true)::bigint as videos_marked_keep,
        COALESCE(SUM(o.storage_size) FILTER (
            WHERE o.keep_forever = false
            AND (o.view_count = 0 OR o.last_viewed_at < cutoff_30)
        ), 0)::bigint as reclaimable_bytes
    FROM owned o;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────────
-- 4. get_cleanup_data(p_user_id uuid, p_never_viewed_days int,
--    p_old_unused_days int, p_limit int) -> jsonb {suggestions, stats, categories}
--    Combined single-call form; suggestions mirror get_cleanup_suggestions,
--    stats mirror get_cleanup_stats. Suggestion objects expose media_id /
--    title / cover_urls (consumed by cleanup_service.get_cleanup_data).
-- ─────────────────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS get_cleanup_data(uuid, integer, integer, integer) CASCADE;

CREATE OR REPLACE FUNCTION get_cleanup_data(
    p_user_id uuid,
    p_never_viewed_days int DEFAULT 7,
    p_old_unused_days int DEFAULT 30,
    p_limit int DEFAULT 50
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    result jsonb;
    cutoff_30 timestamptz := now() - interval '30 days';
BEGIN
    WITH
    owned AS (
        SELECT DISTINCT pm.id,
               pm.title,
               pm.cover_urls,
               pm.author,
               pm.storage_size,
               pm.created_at,
               pm.last_viewed_at,
               pm.view_count,
               pm.keep_forever
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
    ),
    suggestions AS (
        SELECT
            o.id as media_id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            CASE
                WHEN o.view_count = 0 AND o.created_at < now() - (p_never_viewed_days || ' days')::interval THEN 'never_viewed'
                WHEN o.view_count > 0 AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 'old_unused'
                ELSE 'large_file'
            END as reason,
            CASE
                WHEN o.view_count = 0 AND o.created_at < now() - (p_never_viewed_days || ' days')::interval
                    THEN 'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed'
                WHEN o.view_count > 0 AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval
                    THEN 'Not viewed in over ' || p_old_unused_days || ' days'
                ELSE 'Large file: ' || pg_size_pretty(o.storage_size)
            END as reason_detail,
            CASE
                WHEN o.view_count = 0 THEN 1
                WHEN o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 2
                ELSE 3
            END as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND (
            (o.view_count = 0 AND o.created_at < now() - (p_never_viewed_days || ' days')::interval)
            OR (o.view_count > 0 AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval)
            OR (o.storage_size IS NOT NULL AND o.storage_size > (
                SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
                FROM owned sub
                WHERE sub.storage_size IS NOT NULL
            ))
        )
        ORDER BY priority, storage_size DESC NULLS LAST
        LIMIT p_limit
    ),
    stats AS (
        SELECT
            COUNT(*)::bigint as total_videos,
            COALESCE(SUM(o.storage_size), 0)::bigint as total_storage_bytes,
            COUNT(*) FILTER (WHERE o.view_count = 0)::bigint as videos_never_viewed,
            COUNT(*) FILTER (WHERE o.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
            COUNT(*) FILTER (WHERE o.keep_forever = true)::bigint as videos_marked_keep,
            COALESCE(SUM(o.storage_size) FILTER (
                WHERE o.keep_forever = false
                AND (o.view_count = 0 OR o.last_viewed_at < cutoff_30)
            ), 0)::bigint as reclaimable_bytes
        FROM owned o
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

-- PostgREST caches function signatures; force a schema reload so RPC callers
-- pick up the new arg/return shapes.
NOTIFY pgrst, 'reload schema';
