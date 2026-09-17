-- 475_search_text_honours_chip_filters.sql
--
-- The filter chips were silently dropped the moment a keyword search ran.
--
-- My Downloads keeps two independent data paths:
--
--   list    filterBarConfig.toFilterParams() -> fetchLibraryPaginated
--           -> rpc_downloads_library_search   (all 20 chip filters applied)
--   search  textSearch() -> POST /api/v1/search/text
--           -> rpc_user_media_text_search     (no chip filters at all)
--
-- DownloadsView replaces the list wholesale with the search hits once a
-- backend search returns, so every active chip stopped applying -- with no
-- error, and with the chips still rendered as active in the toolbar.
--
-- Measured on production before the fix, for one user's library with the
-- "AI - transcribed" chip on:
--
--   query   matches   transcribed   shown by the UI
--   5000          4             1                 4    <- filter dropped
--   抖音         99             3                99    <- filter dropped
--
-- The first row is why this survived: with four hits nobody counts. The
-- second made it obvious.
--
-- The fix is to give the text-search RPC the same filter predicates the list
-- RPC already has, copied verbatim so the two cannot drift.
--
-- ---------------------------------------------------------------------------
-- Why the old signature is dropped rather than left in place
-- ---------------------------------------------------------------------------
-- The new parameters are a superset with defaults, so an 8-argument call
-- would match BOTH candidates and Postgres raises "function is not unique"
-- rather than picking one. Exactly the situation migration 463 section 3 hit.
-- One implementation must exist, so the old signature goes.
--
-- Deployment order: run-migration and deploy-gpu are independent, so there is
-- no guarantee which lands first (see CLAUDE.md). Migration-first is clean --
-- the old backend's 8-argument call is the one being replaced and it fails
-- loudly. Code-first leaves a short window where the new backend's call finds
-- no matching function; SearchService turns that SQLSTATE 42883 into a typed
-- 503 instead of quietly returning unfiltered rows, which is the failure mode
-- this migration exists to remove.

BEGIN;

DROP FUNCTION IF EXISTS public.rpc_user_media_text_search(
  uuid, text, text[], text, text, text, text[], integer
);

CREATE OR REPLACE FUNCTION public.rpc_user_media_text_search(
  p_user_id  uuid,
  p_pattern  text,
  p_fields   text[] DEFAULT '{}'::text[],
  p_author   text   DEFAULT NULL,
  p_date_from text  DEFAULT NULL,
  p_date_to   text  DEFAULT NULL,
  p_tag_ids  text[] DEFAULT NULL,
  p_limit    integer DEFAULT 1000,
  -- ---- chip filters, mirroring rpc_downloads_library_search ----------------
  -- Same names, same order, same predicates as that function. Keeping them
  -- textually identical is what makes "the list and the search agree" a
  -- reviewable claim rather than a hope.
  p_min_rating     integer DEFAULT NULL,
  p_ai_transcribed boolean DEFAULT NULL,
  p_ai_summarized  boolean DEFAULT NULL,
  p_ai_analyzed    boolean DEFAULT NULL,
  p_has_prompt     boolean DEFAULT NULL,
  p_created_after  text    DEFAULT NULL,
  p_created_before text    DEFAULT NULL,
  p_duration_min   integer DEFAULT NULL,
  p_duration_max   integer DEFAULT NULL,
  p_aspect_ratios  text[]  DEFAULT NULL,
  p_platforms      text[]  DEFAULT NULL,
  p_media_types    text[]  DEFAULT NULL,
  p_has_comments   boolean DEFAULT NULL,
  p_min_likes      integer DEFAULT NULL,
  p_min_comments   integer DEFAULT NULL,
  p_min_favorites  integer DEFAULT NULL,
  p_min_shares     integer DEFAULT NULL,
  p_social_combine text    DEFAULT 'and'
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
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
        OR ('title'       = ANY(p_fields) AND pm.title            ILIKE p_pattern ESCAPE '\')
        OR ('description' = ANY(p_fields) AND pm.description      ILIKE p_pattern ESCAPE '\')
        OR ('author'      = ANY(p_fields) AND pm.author           ILIKE p_pattern ESCAPE '\')
        OR ('hashtags'    = ANY(p_fields) AND pm.hashtags         ILIKE p_pattern ESCAPE '\')
        -- Transcript bodies live in resource_transcripts; ai_extract_text is a
        -- legacy column that only a handful of rows ever populated. Search both
        -- so old and new rows are equally reachable.
        OR ('transcript'  = ANY(p_fields) AND (
              pm.ai_extract_text ILIKE p_pattern ESCAPE '\'
              OR EXISTS (
                SELECT 1 FROM public.resource_transcripts rtx
                WHERE rtx.resource_id = r.id
                  AND rtx.full_text ILIKE p_pattern ESCAPE '\'
              )
           ))
        OR ('notes'       = ANY(p_fields) AND r.notes             ILIKE p_pattern ESCAPE '\')
        OR ('analysis'    = ANY(p_fields) AND EXISTS (
              SELECT 1 FROM public.resource_analysis ra
              WHERE ra.resource_id = r.id
                AND (ra.visual_description ILIKE p_pattern ESCAPE '\'
                     OR ra.detected_text ILIKE p_pattern ESCAPE '\')
           ))
        OR ('tags'        = ANY(p_fields) AND EXISTS (
              SELECT 1
              FROM public.resource_tags rt
              JOIN public.tags t ON t.id = rt.tag_id
              WHERE rt.resource_id = r.id
                AND t.name ILIKE p_pattern ESCAPE '\'
           ))
      )
      -- AND filters (hybrid): author / date range.
      AND (
        p_author IS NULL
        -- p_author is concatenated into a pattern here rather than arriving
        -- ready-made, so the metacharacters are neutralised in place.
        OR pm.author ILIKE (
             '%' ||
             replace(replace(replace(p_author, '\', '\\'), '%', '\%'), '_', '\_') ||
             '%'
           ) ESCAPE '\'
      )
      AND (p_date_from IS NULL OR pm.created_at >= p_date_from::timestamptz)
      AND (p_date_to   IS NULL OR pm.created_at <= p_date_to::timestamptz)
      -- Tag AND: resource must carry EVERY requested tag.
      --
      -- This used to be "at least one" while the list path
      -- (rpc_downloads_library_search) required all of them, so ticking two
      -- tags widened the result set the moment a search took over and
      -- narrowed it again when the search cleared. No caller relied on the
      -- old reading -- every hybridSearch() call site passes {} -- so the two
      -- paths are aligned on the list's meaning, which is the one the user
      -- has already seen.
      AND (
        p_tag_ids IS NULL
        OR cardinality(p_tag_ids) = 0
        OR r.id IN (
          SELECT rt2.resource_id
          FROM public.resource_tags rt2
          WHERE rt2.tag_id = ANY(p_tag_ids::bigint[])
          GROUP BY rt2.resource_id
          HAVING count(DISTINCT rt2.tag_id) = cardinality(p_tag_ids)
        )
      )

      -- ---- chip filters -----------------------------------------------------
      -- Copied verbatim from rpc_downloads_library_search. Both functions
      -- alias resources as ``r`` and parsed_media as ``pm``, so these read
      -- identically in both places and a reviewer can diff them line by line.

      -- Resource-column filters (rating / AI status / dates / duration / aspect)
      AND (p_min_rating IS NULL OR p_min_rating = 0 OR r.rating >= p_min_rating)
      AND (p_ai_transcribed IS NULL OR p_ai_transcribed = false OR r.transcript_status = 'completed')
      AND (p_ai_summarized  IS NULL OR p_ai_summarized  = false OR r.summary_status = 'completed')
      AND (p_ai_analyzed    IS NULL OR p_ai_analyzed    = false OR r.visual_analysis_status = 'completed')
      -- "Has prompt" -- mirrors search_scope_resources' predicate exactly (M1:
      -- btrim + exclude '[]'/'""' text literals + jsonb 'null'/'{}'/'[]').
      AND (
        p_has_prompt IS NULL OR p_has_prompt = false OR (
          (r.gen_prompt IS NOT NULL AND btrim(r.gen_prompt) <> '' AND r.gen_prompt NOT IN ('[]', '""'))
          OR (r.gen_prompt_negative IS NOT NULL AND btrim(r.gen_prompt_negative) <> '' AND r.gen_prompt_negative NOT IN ('[]', '""'))
          OR (r.gen_prompt_json IS NOT NULL AND btrim(r.gen_prompt_json) <> '' AND r.gen_prompt_json NOT IN ('[]', '""'))
          OR (
            r.slide_prompts IS NOT NULL
            AND r.slide_prompts <> 'null'::jsonb
            AND r.slide_prompts <> '{}'::jsonb
            AND r.slide_prompts <> '[]'::jsonb
          )
        )
      )
      -- Day-granular bounds on resources.created_at. Distinct from
      -- p_date_from / p_date_to above, which are /search/hybrid's own filter
      -- contract and compare pm.created_at without the day expansion. No
      -- caller sets both; when one does they simply AND together.
      AND (p_created_after  IS NULL OR r.created_at >= (p_created_after || 'T00:00:00')::timestamptz)
      AND (p_created_before IS NULL OR r.created_at <= (p_created_before || 'T23:59:59.999')::timestamptz)
      AND (p_duration_min IS NULL OR r.duration_seconds >= p_duration_min)
      AND (p_duration_max IS NULL OR r.duration_seconds <= p_duration_max)
      AND (p_aspect_ratios IS NULL OR cardinality(p_aspect_ratios) = 0 OR r.aspect_bucket = ANY(p_aspect_ratios))

      -- parsed_media-column filters (platform / media_type / comments).
      AND (p_platforms IS NULL OR cardinality(p_platforms) = 0 OR pm.source_platform = ANY(p_platforms))
      AND (p_media_types IS NULL OR cardinality(p_media_types) = 0 OR pm.media_type = ANY(p_media_types))
      AND (p_has_comments IS NULL OR p_has_comments = false OR pm.comment_count > 0)

      -- Social thresholds: 'or' combine vs (default) 'and' combine. Thresholds
      -- of 0/NULL are inactive.
      AND (
        CASE
          WHEN p_social_combine = 'or' THEN (
            (p_min_likes IS NOT NULL AND p_min_likes > 0 AND pm.like_count >= p_min_likes)
            OR (p_min_comments IS NOT NULL AND p_min_comments > 0 AND pm.comment_count >= p_min_comments)
            OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0 AND pm.favorite_count >= p_min_favorites)
            OR (p_min_shares IS NOT NULL AND p_min_shares > 0 AND pm.share_count >= p_min_shares)
            -- no social threshold active -> OR group must not exclude rows
            OR NOT (
              (p_min_likes IS NOT NULL AND p_min_likes > 0)
              OR (p_min_comments IS NOT NULL AND p_min_comments > 0)
              OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0)
              OR (p_min_shares IS NOT NULL AND p_min_shares > 0)
            )
          )
          ELSE (
            (p_min_likes IS NULL OR p_min_likes = 0 OR pm.like_count >= p_min_likes)
            AND (p_min_comments IS NULL OR p_min_comments = 0 OR pm.comment_count >= p_min_comments)
            AND (p_min_favorites IS NULL OR p_min_favorites = 0 OR pm.favorite_count >= p_min_favorites)
            AND (p_min_shares IS NULL OR p_min_shares = 0 OR pm.share_count >= p_min_shares)
          )
        END
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
$function$;

-- ---------------------------------------------------------------------------
-- ACL: server-side callers only (migration 463)
-- ---------------------------------------------------------------------------
-- The function takes the target user's uuid as an ordinary argument and runs
-- SECURITY DEFINER, so an untrusted caller reaching it could read any user's
-- library. DROP discarded 463's REVOKE along with the old function, so it has
-- to be re-stated here -- a new function is created with the default PUBLIC
-- EXECUTE grant, which is precisely the hole 463 closed.

REVOKE EXECUTE ON FUNCTION public.rpc_user_media_text_search(
  uuid, text, text[], text, text, text, text[], integer,
  integer, boolean, boolean, boolean, boolean, text, text,
  integer, integer, text[], text[], text[], boolean,
  integer, integer, integer, integer, text
) FROM PUBLIC, anon, authenticated;

COMMIT;
