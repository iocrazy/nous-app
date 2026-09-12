-- 464_scope_resources_search.sql
--
-- "My Uploads" search had two holes, both of which made a typed keyword return
-- the wrong set rather than an error:
--
-- 1. The scope picker offers Name / Notes / Tags and ships with all three
--    ticked, but the PostgREST query behind it only ever matched
--    resources.filename and resources.notes. A resource reachable only by its
--    tag name could not be found, so ticking "Tags" did nothing.
--
-- 2. The moment a tag filter was active the query switched to this function
--    (migration 269 routed it here so tag-AND scales past PostgREST's 1000-row
--    ceiling), and this function had no search parameter at all. The keyword
--    was silently dropped and the user got every resource carrying the tag.
--    resourceService.ts admitted this in a comment and called it out of scope.
--
-- This migration gives the function the search it was missing, so a single
-- implementation answers both routes:
--
--   p_search         the raw term; the pattern is built and escaped in here so
--                    the escaping lives next to the ILIKEs that need it rather
--                    than being duplicated in TypeScript
--   p_search_fields  which of name / notes / tags to scan (NULL or empty means
--                    all three, matching the picker's rule that the user
--                    cannot untick everything)
--   p_folder_ids     the flattened folder set, so "show child files" survives
--                    the switch to this path; the caller computes the
--                    descendants, this function does not walk the tree
--   p_flatten        distinguishes "flatten with no descendants" (scope root:
--                    no folder constraint) from "not flattened" (root only)
--
-- Adding arguments creates a NEW overload rather than replacing the old one,
-- and PostgREST resolves RPCs by argument NAME — with both present a call that
-- omits the new names matches either candidate and errors as ambiguous. So the
-- 27-argument signature is dropped first. The new arguments are all defaulted,
-- which keeps a not-yet-redeployed frontend working against the new function
-- and makes migration-first the safe deployment order.

BEGIN;

DROP FUNCTION IF EXISTS public.search_scope_resources(
  text, boolean, text, text, text[], integer, boolean, boolean, boolean,
  text, text, integer, integer, text[], text[], text[], boolean, integer,
  integer, integer, integer, text, timestamp with time zone, bigint,
  integer, boolean, boolean
);

CREATE OR REPLACE FUNCTION public.search_scope_resources(p_scope_id text, p_is_personal boolean, p_folder_id text DEFAULT NULL::text, p_library_id text DEFAULT NULL::text, p_tag_ids text[] DEFAULT NULL::text[], p_min_rating integer DEFAULT NULL::integer, p_ai_transcribed boolean DEFAULT NULL::boolean, p_ai_summarized boolean DEFAULT NULL::boolean, p_ai_analyzed boolean DEFAULT NULL::boolean, p_created_after text DEFAULT NULL::text, p_created_before text DEFAULT NULL::text, p_duration_min integer DEFAULT NULL::integer, p_duration_max integer DEFAULT NULL::integer, p_aspect_ratios text[] DEFAULT NULL::text[], p_types text[] DEFAULT NULL::text[], p_platforms text[] DEFAULT NULL::text[], p_has_comments boolean DEFAULT NULL::boolean, p_min_likes integer DEFAULT NULL::integer, p_min_comments integer DEFAULT NULL::integer, p_min_favorites integer DEFAULT NULL::integer, p_min_shares integer DEFAULT NULL::integer, p_social_combine text DEFAULT 'and'::text, p_cursor_ts timestamp with time zone DEFAULT NULL::timestamp with time zone, p_cursor_id bigint DEFAULT NULL::bigint, p_limit integer DEFAULT 40, p_with_count boolean DEFAULT false, p_has_prompt boolean DEFAULT NULL::boolean, p_search text DEFAULT NULL::text, p_search_fields text[] DEFAULT NULL::text[], p_folder_ids text[] DEFAULT NULL::text[], p_flatten boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE sql
 STABLE
 SET search_path TO 'public'
AS $function$
  WITH q AS (
    SELECT
      -- Escaped ILIKE pattern, or NULL when there is nothing to search for.
      -- Metacharacters are neutralised here so a user typing "%" matches a
      -- literal percent sign instead of every row; every ILIKE below declares
      -- ESCAPE '\' or the escaping would be inert (Postgres has no default).
      CASE
        WHEN p_search IS NULL OR btrim(p_search) = '' THEN NULL
        ELSE '%' || replace(replace(replace(btrim(p_search), '\', '\\'),
                                    '%', '\%'), '_', '\_') || '%'
      END AS pat,
      -- NULL / empty means "every scope", matching the picker's rule that the
      -- user cannot untick everything.
      COALESCE(
        NULLIF(p_search_fields, '{}'::text[]),
        ARRAY['name', 'notes', 'tags']
      ) AS fields
  ),
  filtered AS (
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
    CROSS JOIN q
    JOIN public.resources r
      ON r.id = ri.resource_id
    LEFT JOIN public.parsed_media pm
      ON pm.id = r.media_id
    WHERE
      -- Base filters (always applied)
      ri.scope_id = p_scope_id::bigint
      AND r.is_trashed = false
      AND r.source_type IS DISTINCT FROM 'web'

      -- Folder. Three shapes:
      --   flatten + an explicit descendant set  -> any folder in that set
      --   flatten with no set (at scope root)   -> no folder constraint
      --   not flattened                         -> the single folder, else root
      -- The caller computes the descendant set; this function does not walk
      -- the folder tree.
      AND (
        CASE
          WHEN p_flatten AND p_folder_ids IS NOT NULL
               AND cardinality(p_folder_ids) > 0
            THEN ri.folder_id = ANY(p_folder_ids::bigint[])
          WHEN p_flatten
            THEN true
          WHEN p_folder_id IS NOT NULL
            THEN ri.folder_id = p_folder_id::bigint
          ELSE ri.folder_id IS NULL
        END
      )

      -- Library: explicit library; if not personal + no library -> NULL only;
      -- personal with no library -> no constraint.
      AND (
        p_library_id IS NOT NULL AND ri.library_id = p_library_id::bigint
        OR (p_library_id IS NULL AND p_is_personal = false AND ri.library_id IS NULL)
        OR (p_library_id IS NULL AND p_is_personal IS DISTINCT FROM false)
      )

      -- Free-text search across the three scopes the picker offers. Before
      -- this migration the function had no search parameter at all, so the
      -- moment a tag filter routed a query here the user's typed keyword was
      -- dropped on the floor and they got every resource carrying the tag.
      AND (
        q.pat IS NULL
        OR ('name'  = ANY(q.fields) AND r.filename ILIKE q.pat ESCAPE '\')
        OR ('notes' = ANY(q.fields) AND r.notes    ILIKE q.pat ESCAPE '\')
        OR ('tags'  = ANY(q.fields) AND EXISTS (
              SELECT 1
              FROM public.resource_tags rt_s
              JOIN public.tags t_s ON t_s.id = rt_s.tag_id
              WHERE rt_s.resource_id = r.id
                AND t_s.name ILIKE q.pat ESCAPE '\'
           ))
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
      -- "Has prompt": any of the 3 text columns non-blank (btrim'd, and not
      -- the literal '[]'/'""' — a serialized empty value isn't a real prompt)
      -- OR slide_prompts (jsonb) non-null and not one of the empty literals.
      -- M1 hardening — no known production rows hit this today, defensive.
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
$function$;

-- This function is called from the browser through PostgREST, so its grants
-- are load-bearing rather than incidental. It takes p_scope_id as an argument
-- and is NOT SECURITY DEFINER, so RLS on resource_items / resources is what
-- actually confines a caller to their own rows — unlike the definer functions
-- revoked in 463, which bypassed RLS and therefore had to be taken away from
-- the browser entirely. Re-grant explicitly: DROP FUNCTION discarded the ACL
-- that migration 269 established.
GRANT EXECUTE ON FUNCTION public.search_scope_resources(
  text, boolean, text, text, text[], integer, boolean, boolean, boolean,
  text, text, integer, integer, text[], text[], text[], boolean, integer,
  integer, integer, integer, text, timestamp with time zone, bigint,
  integer, boolean, boolean, text, text[], text[], boolean
) TO anon, authenticated, service_role;

COMMIT;

-- The signature changed; PostgREST would otherwise keep serving the dropped
-- 27-argument entry from its schema cache. Same closer as 259 / 274 / 463.
NOTIFY pgrst, 'reload schema';
