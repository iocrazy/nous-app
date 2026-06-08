-- 274_user_media_search_rpc.sql
--
-- Scale Tier-1c: kill the 1000-row-capped per-user media allowlist in search.
--
-- `parsed_media` is a GLOBAL table (one row per platform content). Per-user
-- ownership lives on `resources` (creator_id uuid + media_id FK -> parsed_media.id
-- + source_type='web' + is_trashed=false). Every "my library" search used to
-- first build the user's FULL media_id allowlist via
--   client.table("resources").select("media_id").eq("creator_id", uid)...
-- then filter parsed_media with `.in_("id", allowlist)`. PostgREST caps that
-- allowlist fetch at 1000 rows, so at >1000 owned resources search silently
-- dropped everything past the first 1000 (and the giant `.in_()` risked a Kong
-- URL-length 502). The current max user has 841 web resources — LATENT today,
-- breaks the moment a ~100k Eagle import lands in one library.
--
-- Fix: push the user-scope into the query via a JOIN RPC. No allowlist
-- round-trip, no cap, no URL blow-up.
--
-- SECURITY DEFINER (owner postgres): the callers use the service-role admin
-- client (NOT a user JWT), so RLS-based scoping is unavailable — we filter
-- `r.creator_id = p_user_id::uuid` explicitly inside the function instead.
-- This mirrors the existing service-role REST path's behaviour exactly.
--
-- Two functions:
--   1. rpc_user_media_text_search  — multi-field ILIKE over the user's media,
--      returns the card-view parsed_media projection (matches
--      MediaRepository.CARD_SELECT) + resource_id + AI status, DISTINCT per
--      media, ORDER BY created_at DESC LIMIT. Covers BOTH the text-search
--      endpoint and the hybrid-search query/no-query/analysis-fallback paths.
--   2. rpc_user_owned_platform_ids — given a (small, ranker-output) array of
--      platform_ids, returns the subset the user owns. Replaces "fetch full
--      capped allowlist then intersect in Python" in search hydration. Bounded
--      by the input array -> no cap, no 502.

-- ---------------------------------------------------------------------------
-- 1. Multi-field text search, fully user-scoped via JOIN.
-- ---------------------------------------------------------------------------
-- p_pattern is a READY ILIKE pattern (e.g. '%foo%') built + sanitized by the
-- caller. p_pattern IS NULL means "match all" (the hybrid no-query / filter-only
-- path). p_fields selects which scopes participate in the OR-match:
--   title / description / author / hashtags  -> parsed_media columns
--   transcript                               -> parsed_media.ai_extract_text
--   notes                                    -> resources.notes (per-user)
--   tags                                     -> resource_tags -> tags.name ILIKE
--   analysis                                 -> resource_analysis
--                                               (visual_description / detected_text)
-- p_author / p_date_from / p_date_to are AND filters (hybrid). p_tag_ids is an
-- AND filter: the resource must carry at least one of the given tag ids.
CREATE OR REPLACE FUNCTION public.rpc_user_media_text_search(
  p_user_id   uuid,
  p_pattern   text,
  p_fields    text[] DEFAULT '{}',
  p_author    text DEFAULT NULL,
  p_date_from text DEFAULT NULL,
  p_date_to   text DEFAULT NULL,
  p_tag_ids   text[] DEFAULT NULL,
  p_limit     int DEFAULT 1000
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH matched AS (
    SELECT DISTINCT ON (pm.id)
      pm.id          AS pm_id,
      pm.created_at  AS pm_created_at,
      jsonb_build_object(
        'id',                     pm.id,
        'platform_id',            pm.platform_id,
        'source_platform',        pm.source_platform,
        'title',                  pm.title,
        'author',                 pm.author,
        'description',            pm.description,
        'original_url',           pm.original_url,
        'cover_urls',             pm.cover_urls,
        'dynamic_cover_url',      pm.dynamic_cover_url,
        'cover_download_status',  pm.cover_download_status,
        'cover_download_path',    pm.cover_download_path,
        'like_count',             pm.like_count,
        'comment_count',          pm.comment_count,
        'share_count',            pm.share_count,
        'favorite_count',         pm.favorite_count,
        'view_count',             pm.view_count,
        'extract_audio_path',     pm.extract_audio_path,
        'music_download_path',    pm.music_download_path,
        'music_download_status',  pm.music_download_status,
        'music_name',             pm.music_name,
        'music_play_urls',        pm.music_play_urls,
        'video_download_status',  pm.video_download_status,
        'video_download_urls',    pm.video_download_urls,
        'image_download_status',  pm.image_download_status,
        'image_download_urls',    pm.image_download_urls,
        'image_download_path',    pm.image_download_path,
        'hashtags',               pm.hashtags,
        'error_message',          pm.error_message,
        'created_at',             pm.created_at,
        'updated_at',             pm.updated_at,
        'published_at',           pm.published_at,
        'last_viewed_at',         pm.last_viewed_at,
        'media_type',             pm.media_type,
        'media_format',           pm.media_format,
        'duration',               pm.duration,
        'resolution',             pm.resolution,
        'datasize',               pm.datasize,
        'datasize_bytes',         pm.datasize_bytes,
        'storage_size',           pm.storage_size,
        'keep_forever',           pm.keep_forever,
        'hls_path',               pm.hls_path,
        'download_path',          pm.download_path,
        'download_time',          pm.download_time,
        'download_duration',      pm.download_duration,
        -- Per-user decoration (so the card click navigates to the right
        -- /resources/file/<id> URL and the AI dot icons reflect real state).
        'resource_id',            r.id::text,
        'transcript_status',      r.transcript_status,
        'summary_status',         r.summary_status,
        'visual_analysis_status', r.visual_analysis_status
      ) AS row
    FROM public.parsed_media pm
    JOIN public.resources r
      ON  r.media_id    = pm.id
      AND r.creator_id  = p_user_id
      AND r.source_type = 'web'
      AND r.is_trashed  = false
    WHERE
      -- Field OR-match. NULL pattern = match-all (filter-only path).
      (
        p_pattern IS NULL
        OR ('title'       = ANY(p_fields) AND pm.title           ILIKE p_pattern)
        OR ('description' = ANY(p_fields) AND pm.description      ILIKE p_pattern)
        OR ('author'      = ANY(p_fields) AND pm.author           ILIKE p_pattern)
        OR ('hashtags'    = ANY(p_fields) AND pm.hashtags         ILIKE p_pattern)
        OR ('transcript'  = ANY(p_fields) AND pm.ai_extract_text  ILIKE p_pattern)
        OR ('notes'       = ANY(p_fields) AND r.notes             ILIKE p_pattern)
        OR ('analysis'    = ANY(p_fields) AND EXISTS (
              SELECT 1 FROM public.resource_analysis ra
              WHERE ra.resource_id = r.id
                AND (ra.visual_description ILIKE p_pattern
                     OR ra.detected_text ILIKE p_pattern)
           ))
        OR ('tags'        = ANY(p_fields) AND EXISTS (
              SELECT 1
              FROM public.resource_tags rt
              JOIN public.tags t ON t.id = rt.tag_id
              WHERE rt.resource_id = r.id
                AND t.name ILIKE p_pattern
           ))
      )
      -- AND filters (hybrid): author / date range.
      AND (p_author    IS NULL OR pm.author ILIKE ('%' || p_author || '%'))
      AND (p_date_from IS NULL OR pm.created_at >= p_date_from::timestamptz)
      AND (p_date_to   IS NULL OR pm.created_at <= p_date_to::timestamptz)
      -- AND filter (hybrid): resource must carry at least one requested tag id.
      AND (
        p_tag_ids IS NULL
        OR cardinality(p_tag_ids) = 0
        OR EXISTS (
          SELECT 1 FROM public.resource_tags rt2
          WHERE rt2.resource_id = r.id
            AND rt2.tag_id = ANY(p_tag_ids::bigint[])
        )
      )
    -- DISTINCT ON requires the dedup key to lead the ORDER BY; pick the newest
    -- owning resource per media for deterministic decoration.
    ORDER BY pm.id, r.created_at DESC
  )
  SELECT jsonb_build_object(
    'rows',
    COALESCE(
      (
        SELECT jsonb_agg(page.row ORDER BY page.pm_created_at DESC, page.pm_id DESC)
        FROM (
          SELECT row, pm_created_at, pm_id
          FROM matched
          ORDER BY pm_created_at DESC, pm_id DESC
          LIMIT p_limit
        ) AS page
      ),
      '[]'::jsonb
    )
  );
$$;

GRANT EXECUTE ON FUNCTION public.rpc_user_media_text_search(
  uuid, text, text[], text, text, text, text[], int
) TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. Ownership intersection for hydration.
-- ---------------------------------------------------------------------------
-- Given the (already small, top-N ranker output) platform_ids, return the
-- subset the user owns. Replaces "fetch full capped allowlist then intersect".
CREATE OR REPLACE FUNCTION public.rpc_user_owned_platform_ids(
  p_user_id      uuid,
  p_platform_ids text[]
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT COALESCE(jsonb_agg(DISTINCT pm.platform_id), '[]'::jsonb)
  FROM public.parsed_media pm
  JOIN public.resources r
    ON  r.media_id    = pm.id
    AND r.creator_id  = p_user_id
    AND r.source_type = 'web'
    AND r.is_trashed  = false
  WHERE pm.platform_id = ANY(p_platform_ids);
$$;

GRANT EXECUTE ON FUNCTION public.rpc_user_owned_platform_ids(uuid, text[])
  TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
