-- 275_downloads_library_search_rpc.sql
--
-- Scale-100k Tier 1b (tag-intersection half).
--
-- The Downloads view (`fetchLibraryPaginated` in frontend/services/dataService.ts)
-- queries `resources` WHERE creator_id = me AND source_type = 'web', inner-joined
-- to `parsed_media`, keyset-paginated on (resources.created_at, resources.id).
-- Tag-AND filtering was done CLIENT-side via `resolveLibraryTagIntersection`:
-- it built a resource_id set with `.eq('tag_id', t)` per tag, then passed the
-- set back as `.in('id', ids)`. Two scale bugs at 100k:
--   1. each `.eq('tag_id')` SELECT silently truncates at PostgREST's 1000-row
--      cap → a popular tag's intersection is computed from a partial set →
--      WRONG results (rows silently missing).
--   2. the resulting `.in('id', [huge list])` overflows the Kong/nginx URL
--      ceiling → 502.
--
-- This mirrors the resource-library fix (mig 269 `search_scope_resources`),
-- but over the Downloads base (resources.source_type='web' ⋈ parsed_media,
-- keyed by resources.id) and with the exact filter set of `applyLibraryFilters`.
-- Tag-AND runs in SQL (GROUP BY … HAVING count(DISTINCT tag)=N), so it scales
-- past 1000 and never builds a giant URL.
--
-- SECURITY INVOKER (same posture as mig 269): RLS on `resources` still applies,
-- and the explicit `r.creator_id = p_user_id` predicate means a spoofed
-- p_user_id intersects the RLS-permitted (own) rows to the empty set.
--
-- p_media_types carries the ALREADY-MAPPED parsed_media.media_type wire values
-- (MEDIA_TYPE_VALUES_BY_TYPE in dataService is the single source of truth). The
-- caller passes ['__impossible__'] for a type group with no wire values
-- (document/other), preserving today's "force zero rows" behaviour.

CREATE OR REPLACE FUNCTION public.rpc_downloads_library_search(
  p_user_id        uuid,
  p_tag_ids        text[] DEFAULT NULL,
  p_min_rating     int DEFAULT NULL,
  p_ai_transcribed boolean DEFAULT NULL,
  p_ai_summarized  boolean DEFAULT NULL,
  p_ai_analyzed    boolean DEFAULT NULL,
  p_created_after  text DEFAULT NULL,
  p_created_before text DEFAULT NULL,
  p_duration_min   int DEFAULT NULL,
  p_duration_max   int DEFAULT NULL,
  p_aspect_ratios  text[] DEFAULT NULL,
  p_platforms      text[] DEFAULT NULL,
  p_media_types    text[] DEFAULT NULL,
  p_has_comments   boolean DEFAULT NULL,
  p_min_likes      int DEFAULT NULL,
  p_min_comments   int DEFAULT NULL,
  p_min_favorites  int DEFAULT NULL,
  p_min_shares     int DEFAULT NULL,
  p_social_combine text DEFAULT 'and',
  p_cursor_ts      timestamptz DEFAULT NULL,
  p_cursor_id      bigint DEFAULT NULL,
  p_limit          int DEFAULT 40,
  p_with_count     boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
  WITH filtered AS (
    SELECT
      r.id         AS res_id,
      r.created_at AS created_at,
      jsonb_build_object(
        'id',         r.id,
        'created_at', r.created_at,
        'parsed_media', to_jsonb(pm.*)
      ) AS row
    FROM public.resources r
    JOIN public.parsed_media pm
      ON pm.id = r.media_id
    WHERE
      -- Base filters (always applied) — mirror dataService's resources query.
      r.creator_id = p_user_id
      AND r.source_type = 'web'
      AND r.is_trashed = false

      -- Tag AND: resource must carry EVERY requested tag.
      AND (
        p_tag_ids IS NULL
        OR cardinality(p_tag_ids) = 0
        OR r.id IN (
          SELECT rt.resource_id
          FROM public.resource_tags rt
          WHERE rt.tag_id = ANY(p_tag_ids::bigint[])
          GROUP BY rt.resource_id
          HAVING count(DISTINCT rt.tag_id) = cardinality(p_tag_ids)
        )
      )

      -- Resource-column filters (rating / AI status / dates / duration / aspect)
      AND (p_min_rating IS NULL OR p_min_rating = 0 OR r.rating >= p_min_rating)
      AND (p_ai_transcribed IS NULL OR p_ai_transcribed = false OR r.transcript_status = 'completed')
      AND (p_ai_summarized  IS NULL OR p_ai_summarized  = false OR r.summary_status = 'completed')
      AND (p_ai_analyzed    IS NULL OR p_ai_analyzed    = false OR r.visual_analysis_status = 'completed')
      AND (p_created_after  IS NULL OR r.created_at >= (p_created_after || 'T00:00:00')::timestamptz)
      AND (p_created_before IS NULL OR r.created_at <= (p_created_before || 'T23:59:59.999')::timestamptz)
      AND (p_duration_min IS NULL OR r.duration_seconds >= p_duration_min)
      AND (p_duration_max IS NULL OR r.duration_seconds <= p_duration_max)
      AND (p_aspect_ratios IS NULL OR cardinality(p_aspect_ratios) = 0 OR r.aspect_bucket = ANY(p_aspect_ratios))

      -- parsed_media-column filters (platform / media_type / comments).
      AND (p_platforms IS NULL OR cardinality(p_platforms) = 0 OR pm.source_platform = ANY(p_platforms))
      AND (p_media_types IS NULL OR cardinality(p_media_types) = 0 OR pm.media_type = ANY(p_media_types))
      AND (p_has_comments IS NULL OR p_has_comments = false OR pm.comment_count > 0)

      -- Social thresholds: 'or' combine vs (default) 'and' combine — matches
      -- applyLibraryFilters' AND-chain / .or() fragment behaviour. Thresholds
      -- of 0/NULL are inactive.
      AND (
        CASE
          WHEN p_social_combine = 'or' THEN (
            (p_min_likes IS NOT NULL AND p_min_likes > 0 AND pm.like_count >= p_min_likes)
            OR (p_min_comments IS NOT NULL AND p_min_comments > 0 AND pm.comment_count >= p_min_comments)
            OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0 AND pm.favorite_count >= p_min_favorites)
            OR (p_min_shares IS NOT NULL AND p_min_shares > 0 AND pm.share_count >= p_min_shares)
            -- no social threshold active → OR group must not exclude rows
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
  )
  SELECT jsonb_build_object(
    'rows',
    COALESCE(
      (
        SELECT jsonb_agg(page.row ORDER BY page.created_at DESC, page.res_id DESC)
        FROM (
          SELECT f.row, f.created_at, f.res_id
          FROM filtered f
          WHERE (
            p_cursor_ts IS NULL
            OR f.created_at < p_cursor_ts
            OR (f.created_at = p_cursor_ts AND f.res_id < p_cursor_id)
          )
          ORDER BY f.created_at DESC, f.res_id DESC
          LIMIT p_limit
        ) AS page
      ),
      '[]'::jsonb
    ),
    'total_count',
    CASE WHEN p_with_count THEN (SELECT count(*) FROM filtered) ELSE NULL END
  );
$$;

GRANT EXECUTE ON FUNCTION public.rpc_downloads_library_search(
  uuid, text[], int, boolean, boolean, boolean, text, text, int, int,
  text[], text[], text[], boolean, int, int, int, int, text,
  timestamptz, bigint, int, boolean
) TO authenticated;
