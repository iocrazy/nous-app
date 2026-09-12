-- 463_search_transcript_and_embedding_scope.sql
--
-- Two search defects found on 2026-09-11 while auditing the library search box.
--
-- 1. The "AI transcript" scope checkbox read ``parsed_media.ai_extract_text``,
--    a near-empty legacy column. The real transcript bodies live in
--    ``resource_transcripts.full_text`` (the pilot user had 1 non-empty
--    ai_extract_text row against 31 transcript rows / 44 resources marked
--    transcript_status='completed'), so ticking the box found nothing.
--    The old column is kept in the OR so pre-migration rows stay reachable.
--
-- 2. SECURITY (found while auditing the above, fixed here because a
--    CREATE OR REPLACE preserves the old ACL and would otherwise leave the
--    widened function exposed): migration 274 left
--    ``rpc_user_media_text_search`` and ``rpc_user_owned_platform_ids``
--    EXECUTE-able by PUBLIC / anon / authenticated while they are
--    SECURITY DEFINER and take the target user's uuid as a plain argument
--    with no ``auth.uid()`` check. Anyone holding the publishable anon key —
--    which ships inside the browser bundle — could POST another user's uuid
--    to /rest/v1/rpc/ and read that user's whole library, notes included.
--    Nothing legitimate uses that path: the backend reaches these functions
--    over a direct SQL session as the owning role, and no frontend code
--    calls them. So the grants are revoked rather than guarded.
--
-- 3. ``match_videos_by_embedding`` scanned every user's analysis rows. The
--    router filtered other people's hits away afterwards, so nothing leaked,
--    but the RPC had already spent its whole ``match_count`` budget ranking
--    them — a user could get an empty page while owning matching rows. The new
--    ``p_user_id`` argument pushes ownership into the scan. It is nullable and
--    defaults to NULL (= no scoping) so the signature stays backward
--    compatible for any caller that has not been redeployed yet.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. transcript scope reads the real transcript table
-- ---------------------------------------------------------------------------
--
-- Every ILIKE below declares ESCAPE '\'. Postgres has no default LIKE escape
-- character, so without this the caller's escaping (app/services/library/
-- like_escape.py) would be inert and a user typing a bare "%" would still get
-- a match-everything scan over every column, transcript bodies included.
CREATE OR REPLACE FUNCTION public.rpc_user_media_text_search(
  p_user_id  uuid,
  p_pattern  text,
  p_fields   text[] DEFAULT '{}'::text[],
  p_author   text   DEFAULT NULL,
  p_date_from text  DEFAULT NULL,
  p_date_to   text  DEFAULT NULL,
  p_tag_ids  text[] DEFAULT NULL,
  p_limit    integer DEFAULT 1000
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
$function$;

-- No trigram index on resource_transcripts.full_text here, deliberately. The
-- transcript branch is a CORRELATED subquery (``rtx.resource_id = r.id`` AND
-- the ILIKE), so the planner locates the single owning row through the existing
-- unique index and applies the ILIKE as a filter. A GIN trgm index over
-- multi-KB bodies would never drive that plan — it would only add write
-- amplification on a REPLICA IDENTITY FULL table. Making the transcript scope
-- index-driven needs the subquery inverted (match full_text first, semi-join
-- resource_id back), which is a query-shape change, not an index.

-- ---------------------------------------------------------------------------
-- 2. the user-scoped search RPCs stop being reachable from the browser
-- ---------------------------------------------------------------------------
--
-- Both take the target user's uuid as an ordinary argument. That is fine for a
-- trusted server-side caller and catastrophic for an untrusted one, so the
-- answer is to make the untrusted caller unable to reach them at all. The
-- owning role (and service_role, used by server-side tooling) keeps EXECUTE.
--
-- Re-runnable: REVOKE on an already-revoked grant is a no-op.

REVOKE EXECUTE ON FUNCTION public.rpc_user_media_text_search(
  uuid, text, text[], text, text, text, text[], integer
) FROM PUBLIC, anon, authenticated;

REVOKE EXECUTE ON FUNCTION public.rpc_user_owned_platform_ids(uuid, text[])
  FROM PUBLIC, anon, authenticated;

-- tags.name is now searched by default (the scope widened from 4 fields to 6),
-- and baseline only carries a btree index on it, which a leading-wildcard
-- ILIKE cannot use. 143 rows today, so the build is instant; the index is what
-- keeps the default scope cheap as the tag vocabulary grows.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_tags_name_trgm
  ON public.tags USING gin (name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 3. embedding search is scoped to the caller
-- ---------------------------------------------------------------------------
-- Adding an argument makes this a NEW overload rather than a replacement. With
-- both present, a 3-arg call matches either candidate and Postgres raises
-- "function is not unique" rather than quietly picking one. Drop the old
-- signature so exactly one implementation exists.
DROP FUNCTION IF EXISTS public.match_videos_by_embedding(
  vector, double precision, integer
);

CREATE OR REPLACE FUNCTION public.match_videos_by_embedding(
  query_embedding vector,
  match_threshold double precision DEFAULT 0.7,
  match_count integer DEFAULT 10,
  p_user_id uuid DEFAULT NULL
)
RETURNS TABLE(
  media_id bigint,
  platform_id text,
  title text,
  description text,
  cover_urls jsonb,
  author text,
  view_count bigint,
  created_at timestamp with time zone,
  similarity double precision
)
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_catalog'
AS $function$
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
      -- NULL p_user_id keeps the pre-463 global behaviour so an un-redeployed
      -- caller does not start returning empty pages mid-rollout.
      AND (p_user_id IS NULL OR r.creator_id = p_user_id)
      AND (1 - (ra.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY ra.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$function$;

-- Not SECURITY DEFINER, but it answers "what is semantically near this vector"
-- across every user's analysis rows, so it has no business being callable from
-- the browser either. Revoked here rather than in section 2 because the 4-arg
-- signature has to exist before it can be named.
REVOKE EXECUTE ON FUNCTION public.match_videos_by_embedding(
  vector, double precision, integer, uuid
) FROM PUBLIC, anon, authenticated;

COMMIT;

-- The 3-arg overload was dropped; drop it from PostgREST's schema cache too.
-- Same closer as migrations 259 and 274, which also changed this RPC's shape.
NOTIFY pgrst, 'reload schema';
