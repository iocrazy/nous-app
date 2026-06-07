-- 269_search_scope_resources_rpc.sql
--
-- Purpose: server-side filtered + keyset-paginated search over a scope's
-- resource library. This replaces a frontend PostgREST query that combined
-- tag-AND filtering with a client-built `.in(ids)` list. At 100k scale that
-- approach breaks two ways:
--   1. PostgREST caps a single response at 1000 rows, so the candidate-id
--      prefetch silently truncates.
--   2. The follow-up `.in(ids)` request blows past the URL-length limit.
-- Pushing the whole filter + tag-AND + pagination into one RPC keeps the
-- query fully server-side and lets tag-AND scale past the 1000-row cap.
--
-- Returns a single jsonb:
--   { "rows": [ ...up to p_limit keyset-ordered rows... ],
--     "total_count": <bigint when p_with_count else null> }
--
-- Ordering / keyset cursor: ORDER BY created_at DESC, item_id DESC, cursor
-- is the (created_at, item_id) pair of the last row of the previous page.

CREATE OR REPLACE FUNCTION public.search_scope_resources(
  p_scope_id       text,
  p_is_personal    boolean,
  p_folder_id      text DEFAULT NULL,
  p_library_id     text DEFAULT NULL,
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
  p_types          text[] DEFAULT NULL,
  p_platforms      text[] DEFAULT NULL,
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
      ri.id         AS item_id,
      ri.created_at AS created_at,
      to_jsonb(ri.*) || jsonb_build_object(
        'resource',
        to_jsonb(r.*) || jsonb_build_object(
          'media',
          CASE WHEN pm.id IS NOT NULL THEN jsonb_build_object(
            'id',             pm.id,
            'source_platform', pm.source_platform,
            'like_count',     pm.like_count,
            'comment_count',  pm.comment_count,
            'favorite_count', pm.favorite_count,
            'share_count',    pm.share_count
          ) ELSE NULL END
        )
      ) AS row
    FROM public.resource_items ri
    JOIN public.resources r
      ON r.id = ri.resource_id
    LEFT JOIN public.parsed_media pm
      ON pm.id = r.media_id
    WHERE
      -- Base filters (always applied)
      ri.scope_id = p_scope_id::bigint
      AND r.is_trashed = false
      AND r.source_type IS DISTINCT FROM 'web'

      -- Folder: explicit folder, else root (NULL)
      AND (
        (p_folder_id IS NOT NULL AND ri.folder_id = p_folder_id::bigint)
        OR (p_folder_id IS NULL AND ri.folder_id IS NULL)
      )

      -- Library: explicit library; if not personal + no library -> NULL only;
      -- personal with no library -> no constraint.
      AND (
        p_library_id IS NOT NULL AND ri.library_id = p_library_id::bigint
        OR (p_library_id IS NULL AND p_is_personal = false AND ri.library_id IS NULL)
        OR (p_library_id IS NULL AND p_is_personal IS DISTINCT FROM false)
      )

      -- Tag AND: resource must carry EVERY requested tag
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

      -- Resource-column filters
      AND (p_min_rating IS NULL OR r.rating >= p_min_rating)
      AND (p_ai_transcribed IS NULL OR p_ai_transcribed = false OR r.transcript_status = 'completed')
      AND (p_ai_summarized  IS NULL OR p_ai_summarized  = false OR r.summary_status = 'completed')
      AND (p_ai_analyzed    IS NULL OR p_ai_analyzed    = false OR r.visual_analysis_status = 'completed')
      AND (p_created_after  IS NULL OR r.created_at >= (p_created_after || 'T00:00:00')::timestamptz)
      AND (p_created_before IS NULL OR r.created_at <= (p_created_before || 'T23:59:59.999')::timestamptz)
      AND (p_duration_min IS NULL OR r.duration_seconds >= p_duration_min)
      AND (p_duration_max IS NULL OR r.duration_seconds <= p_duration_max)
      AND (p_aspect_ratios IS NULL OR r.aspect_bucket = ANY(p_aspect_ratios))

      -- Type filter: OR across the selected mime-type prefix groups
      AND (
        p_types IS NULL
        OR (
          ('video' = ANY(p_types) AND r.mime_type LIKE 'video/%')
          OR ('image' = ANY(p_types) AND r.mime_type LIKE 'image/%')
          OR ('audio' = ANY(p_types) AND r.mime_type LIKE 'audio/%')
          OR ('document' = ANY(p_types) AND (
                r.mime_type LIKE 'application/pdf%'
                OR r.mime_type LIKE 'application/msword%'
                OR r.mime_type LIKE 'application/vnd.%'
                OR r.mime_type LIKE 'text/%'
          ))
          OR ('other' = ANY(p_types) AND (
                r.mime_type NOT LIKE 'video/%'
                AND r.mime_type NOT LIKE 'image/%'
                AND r.mime_type NOT LIKE 'audio/%'
                AND r.mime_type NOT LIKE 'application/pdf%'
                AND r.mime_type NOT LIKE 'application/msword%'
                AND r.mime_type NOT LIKE 'application/vnd.%'
                AND r.mime_type NOT LIKE 'text/%'
          ))
        )
      )

      -- parsed_media-column filters
      AND (p_platforms IS NULL OR pm.source_platform = ANY(p_platforms))
      AND (p_has_comments IS NULL OR p_has_comments = false OR pm.comment_count > 0)

      -- Social thresholds: 'or' combine vs (default) 'and' combine
      AND (
        CASE
          WHEN p_social_combine = 'or' THEN (
            (p_min_likes IS NOT NULL AND p_min_likes > 0 AND pm.like_count >= p_min_likes)
            OR (p_min_comments IS NOT NULL AND p_min_comments > 0 AND pm.comment_count >= p_min_comments)
            OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0 AND pm.favorite_count >= p_min_favorites)
            OR (p_min_shares IS NOT NULL AND p_min_shares > 0 AND pm.share_count >= p_min_shares)
            -- if no social threshold is set, the OR group must not exclude rows
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
        SELECT jsonb_agg(page.row ORDER BY page.created_at DESC, page.item_id DESC)
        FROM (
          SELECT f.row, f.created_at, f.item_id
          FROM filtered f
          WHERE (
            p_cursor_ts IS NULL
            OR f.created_at < p_cursor_ts
            OR (f.created_at = p_cursor_ts AND f.item_id < p_cursor_id)
          )
          ORDER BY f.created_at DESC, f.item_id DESC
          LIMIT p_limit
        ) AS page
      ),
      '[]'::jsonb
    ),
    'total_count',
    CASE WHEN p_with_count THEN (SELECT count(*) FROM filtered) ELSE NULL END
  );
$$;

GRANT EXECUTE ON FUNCTION public.search_scope_resources(
  text, boolean, text, text, text[], int, boolean, boolean, boolean,
  text, text, int, int, text[], text[], text[], boolean, int, int, int,
  int, text, timestamptz, bigint, int, boolean
) TO authenticated;

NOTIFY pgrst, 'reload schema';
