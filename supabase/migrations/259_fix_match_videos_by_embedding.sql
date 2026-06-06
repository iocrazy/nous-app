-- =============================================================
-- 259: Repair match_videos_by_embedding() RPC (schema drift)
-- =============================================================
-- Root cause — the last definition (mig 066) read from the
-- pre-rename schema and a since-dropped column, so every call
-- 500s (vector search dead end-to-end):
--
--   * mig 076 (unify_media_to_resource): table `media_analysis`
--     was RENAMED to `resource_analysis` and its `media_id`
--     column RENAMED to `resource_id` (FK now resource_id ->
--     resources.id). mig 066 still did
--       FROM media_analysis ma JOIN parsed_media pm
--            ON pm.id = ma.media_id
--     -> relation "media_analysis" / column "media_id" gone.
--
--   * mig 086 (drop_unused_parsed_media_columns): dropped
--     `parsed_media.cover_url`. mig 066 still SELECTed
--     `pm.cover_url` -> column gone. Cover URLs now live in the
--     `parsed_media.cover_urls` jsonb array (mig 004).
--
-- Fix — re-source the join through the new shape:
--   resource_analysis ra -> resources r -> parsed_media pm
-- and project cover_urls (jsonb array) instead of the dropped
-- scalar cover_url. The consumer
-- (search_service.SemanticSearchResult, ~line 86-94) reads
-- r["media_id"] (bracket access -> column MUST be `media_id`,
-- NOT mig 066's `video_id`) and (r.get("cover_urls") or [None])[0]
-- (plural array -> column MUST be `cover_urls`).
--
-- DROP-first: the RETURNS TABLE shape changes (video_id ->
-- media_id, cover_url -> cover_urls), and Postgres forbids
-- changing a function's return type via CREATE OR REPLACE.
-- =============================================================

DROP FUNCTION IF EXISTS match_videos_by_embedding(vector, double precision, integer);

CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    media_id bigint,
    platform_id text,
    title text,
    description text,
    cover_urls jsonb,
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
        pm.id,
        -- platform_id / author are varchar(255) on parsed_media; cast to text
        -- so the projection matches the RETURNS TABLE declared types (plpgsql
        -- RETURN QUERY type-checks strictly: varchar != text would 42804).
        pm.platform_id::text,
        pm.title,
        pm.description,
        pm.cover_urls,
        pm.author::text,
        COALESCE(pm.view_count, 0)::bigint,
        pm.created_at,
        (1 - (ra.content_embedding <=> query_embedding))::float
    FROM resource_analysis ra
    JOIN resources r     ON r.id = ra.resource_id
    JOIN parsed_media pm ON pm.id = r.media_id
    WHERE ra.content_embedding IS NOT NULL
      AND r.media_id IS NOT NULL
      AND (1 - (ra.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY ra.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Pin search_path per the mig 127 convention (lint
-- 0011_function_search_path_mutable). mig 066 defined this fn
-- plain (no SECURITY DEFINER) and mig 127 never re-pinned it
-- because it was dead/uncallable; pin it now.
ALTER FUNCTION public.match_videos_by_embedding(vector, double precision, integer)
    SET search_path = public, pg_catalog;

NOTIFY pgrst, 'reload schema';
