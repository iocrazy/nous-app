-- 490: embedding-space identifier next to every pgvector column.
--
-- A vector only means something next to vectors from the same model. All four
-- vector columns are vector(2048) and have only ever been filled by
-- doubao-embedding-vision, but nothing recorded that: switching the embedder
-- to another 2048-wide model would put two unrelated spaces in one column and
-- `<=>` would compare them with confident, meaningless scores.
--
-- 1. `embedding_model text` next to each vector. Writers stamp the embedder's
--    ACTUAL provider model id (not the catalog name, which can be renamed —
--    485/488 did — without the space changing). See
--    backend/app/core/embedding_space.py.
--
--    Existing rows stay NULL = "legacy, assumed current space". Their model
--    cannot be derived deterministically here: the embedder is resolved at
--    runtime through three fallbacks (governance catalog pick → manual config
--    → graph_embedder_*), the catalog row is not seeded by any migration, and
--    two doubao-embedding-vision versions (250615 / 251215) have been
--    configured over time. Guessing a string would be worse than admitting we
--    do not know. Readers treat NULL as matching any space, which is true
--    today; before the next embedder switch, stamp NULL rows with the old
--    model id (SQL in embedding_space.py's docstring).
--
-- 2. match_videos_by_embedding gains `p_embedding_model text DEFAULT NULL` and
--    only ranks rows of the query's space (or NULL). Adding a parameter makes
--    a NEW overload, so the 4-arg signature is dropped first (two overloads
--    would make every 4-arg call "function is not unique"). With the DEFAULT,
--    un-redeployed 4-arg callers keep working — deploy-order safe in the
--    migration-first direction; the code-first direction falls back to the
--    4-arg call on SQLSTATE 42883 (analysis_repository.search_by_embedding).
--    The ACL does not carry over to a new signature: REVOKEd again below,
--    same as 463.
--
-- 3. find_duplicate_videos only pairs rows of the same space. Same signature,
--    CREATE OR REPLACE — which PRESERVES the old ACL, and that ACL was never
--    closed: it is SECURITY DEFINER, takes the target user as a plain
--    parameter, and nothing ever revoked the default PUBLIC EXECUTE, so any
--    anon-key holder could read another user's duplicate list through
--    /rest/v1/rpc. Only the backend (direct SQL, cleanup_service) calls it, so
--    it is REVOKEd from the browser roles here (CLAUDE.md "SECURITY DEFINER").
--
-- Idempotent (IF NOT EXISTS / IF EXISTS / OR REPLACE); safe on an empty DB.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. space columns
-- ---------------------------------------------------------------------------
ALTER TABLE public.resource_analysis
  ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE public.hotspots
  ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE public.topic_groups
  ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE public.user_topic_interests
  ADD COLUMN IF NOT EXISTS embedding_model text;

COMMENT ON COLUMN public.resource_analysis.embedding_model IS
  'Actual provider model id that produced content_embedding. NULL = written before mig 490 (legacy, treated as the current space).';
COMMENT ON COLUMN public.hotspots.embedding_model IS
  'Actual provider model id that produced embedding. NULL = written before mig 490 (legacy, treated as the current space).';
COMMENT ON COLUMN public.topic_groups.embedding_model IS
  'Actual provider model id of the centroid embedding. NULL = written before mig 490 (legacy, treated as the current space).';
COMMENT ON COLUMN public.user_topic_interests.embedding_model IS
  'Actual provider model id that produced embedding. NULL = written before mig 490 (legacy, treated as the current space).';

-- ---------------------------------------------------------------------------
-- 2. match_videos_by_embedding: + p_embedding_model
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.match_videos_by_embedding(
  vector, double precision, integer, uuid
);

CREATE OR REPLACE FUNCTION public.match_videos_by_embedding(
  query_embedding vector,
  match_threshold double precision DEFAULT 0.7,
  match_count integer DEFAULT 10,
  p_user_id uuid DEFAULT NULL,
  p_embedding_model text DEFAULT NULL
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
    -- Body as in 476 plus the space filter. DISTINCT ON forces its own ORDER
    -- BY to start with pm.id, so LIMIT at that level would return the lowest
    -- ids, not the closest: the inner query keeps one row per media (its best
    -- distance), the outer ranks by distance.
    SELECT
        d.media_id,
        d.platform_id,
        d.title,
        d.description,
        d.cover_urls,
        d.author,
        d.view_count,
        d.created_at,
        (1 - d.distance)::float AS similarity
    FROM (
        SELECT DISTINCT ON (pm.id)
            pm.id                        AS media_id,
            pm.platform_id::text         AS platform_id,
            pm.title,
            pm.description,
            pm.cover_urls,
            pm.author::text              AS author,
            COALESCE(pm.view_count, 0)::bigint AS view_count,
            pm.created_at,
            (ra.content_embedding <=> query_embedding) AS distance
        FROM resource_analysis ra
        JOIN resources r     ON r.id = ra.resource_id
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE ra.content_embedding IS NOT NULL
          AND r.media_id IS NOT NULL
          AND r.is_trashed = false
          AND r.source_type = 'web'
          AND (p_user_id IS NULL OR r.creator_id = p_user_id)
          -- Same space as the query; NULL on either side = legacy/current.
          AND (p_embedding_model IS NULL
               OR ra.embedding_model IS NULL
               OR ra.embedding_model = p_embedding_model)
          AND (1 - (ra.content_embedding <=> query_embedding)) > match_threshold
        ORDER BY pm.id, ra.content_embedding <=> query_embedding
    ) d
    ORDER BY d.distance
    LIMIT match_count;
END;
$function$;

REVOKE EXECUTE ON FUNCTION public.match_videos_by_embedding(
  vector, double precision, integer, uuid, text
) FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. find_duplicate_videos: same-space pairs only (+ close the ACL)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.find_duplicate_videos(
    p_user_id uuid,
    similarity_threshold double precision DEFAULT 0.85,
    max_results integer DEFAULT 20
)
RETURNS TABLE (
    media_id bigint,
    title text,
    cover_urls jsonb,
    author character varying,
    storage_size bigint,
    created_at timestamp with time zone,
    last_viewed_at timestamp with time zone,
    view_count integer,
    similar_to bigint,
    similarity_score double precision
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
BEGIN
    RETURN QUERY
    WITH user_media AS (
        SELECT pm.id, ra.content_embedding, ra.embedding_model
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
        -- Same space only; NULL on either side = legacy/current.
        WHERE (um1.embedding_model IS NULL
               OR um2.embedding_model IS NULL
               OR um1.embedding_model = um2.embedding_model)
          AND (1 - (um1.content_embedding <=> um2.content_embedding)) >= similarity_threshold
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

REVOKE EXECUTE ON FUNCTION public.find_duplicate_videos(
  uuid, double precision, integer
) FROM PUBLIC, anon, authenticated;

COMMIT;
