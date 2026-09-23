-- 494: resource_embeddings (halfvec 2048 + HNSW) + embedding_spaces.
--
-- resource_analysis.content_embedding is vector(2048): pgvector's HNSW caps
-- `vector` at 2000 dims, so that column has never had an index and every
-- semantic query is a sequential scan. halfvec goes to 4000, so the vectors
-- move to a table of their own, keyed by (resource, layer, space):
--   * layer    — which retrieval layer ('semantic' now; 'transcript' later)
--   * space_id — which embedder produced it (embedding_spaces). A vector only
--     means something next to vectors of the same space; switching models is
--     "new space + re-embed", never "overwrite in place".
-- The old column stays for one release (readers fall back to it while the
-- new table fills); mig 490's embedding_model stamp on resource_analysis is
-- superseded for this table by space_id and left in place.
--
-- find_duplicate_videos now reads ONLY the new table: until the backfill
-- fills it, the cleanup page's duplicate list is empty rather than mixing
-- two stores. (Production held 0 legacy vectors when this was written.)
--
-- Deploy-order safe both ways: old code keeps writing the old column; new
-- code treats a missing table/function (42P01 / 42883) as a typed
-- store_missing.
-- Idempotent; safe on an empty DB.

BEGIN;

CREATE TABLE IF NOT EXISTS public.embedding_spaces (
  id                  bigint PRIMARY KEY DEFAULT public.generate_snowflake_id(),
  actual_model        text NOT NULL,
  protocol            text NOT NULL,
  dims                integer NOT NULL DEFAULT 2048,
  modalities          jsonb NOT NULL DEFAULT '["text"]'::jsonb,
  instruction_version text NOT NULL DEFAULT 'en_keyword_v1',
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT embedding_spaces_model_dims_key UNIQUE (actual_model, dims),
  CONSTRAINT embedding_spaces_dims_check CHECK (dims > 0 AND dims <= 4000)
);
COMMENT ON TABLE public.embedding_spaces IS
  'One row per (embedder model, width). Vectors are only comparable within a space.';

CREATE TABLE IF NOT EXISTS public.resource_embeddings (
  resource_id  bigint NOT NULL REFERENCES public.resources(id) ON DELETE CASCADE,
  layer        text NOT NULL,
  space_id     bigint NOT NULL REFERENCES public.embedding_spaces(id) ON DELETE CASCADE,
  embedding    halfvec(2048) NOT NULL,
  source_hash  text NOT NULL,
  source_text  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT resource_embeddings_pkey PRIMARY KEY (resource_id, layer, space_id),
  CONSTRAINT resource_embeddings_layer_check CHECK (layer IN ('semantic', 'transcript'))
);
COMMENT ON COLUMN public.resource_embeddings.source_hash IS
  'sha1 of the embedded document (+ composer version); unchanged hash = skip re-embed.';

CREATE INDEX IF NOT EXISTS resource_embeddings_embedding_hnsw
  ON public.resource_embeddings USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS resource_embeddings_space_layer_idx
  ON public.resource_embeddings (space_id, layer);

-- Backend-only tables: RLS on, owner-scoped policy mirrors resource_analysis
-- so a browser role that ever reaches them sees only its own rows.
ALTER TABLE public.embedding_spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resource_embeddings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Read embeddings of owned resources" ON public.resource_embeddings;
CREATE POLICY "Read embeddings of owned resources" ON public.resource_embeddings
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.resources r
    WHERE r.id = resource_embeddings.resource_id AND r.creator_id = auth.uid()));
DROP POLICY IF EXISTS "Read embedding spaces" ON public.embedding_spaces;
CREATE POLICY "Read embedding spaces" ON public.embedding_spaces FOR SELECT USING (true);

-- Nearest resources in ONE space and ONE layer. One row per resource by
-- construction (PK), and resources <-> parsed_media is 1:1, so no DISTINCT ON:
-- ORDER BY distance LIMIT n is exactly the shape the HNSW index serves.
-- Backend-only (direct SQL session): REVOKEd from the browser roles below
-- (CLAUDE.md "SECURITY DEFINER" — it is not definer, but p_user_id is a plain
-- argument and a NULL one means "every user", so it must not be reachable
-- through /rest/v1/rpc either).
DROP FUNCTION IF EXISTS public.match_resource_embeddings(halfvec, bigint, text, double precision, integer, uuid);
CREATE FUNCTION public.match_resource_embeddings(
  query_embedding halfvec,
  p_space_id      bigint,
  p_layer         text,
  match_threshold double precision DEFAULT 0.5,
  match_count     integer DEFAULT 10,
  p_user_id       uuid DEFAULT NULL
)
RETURNS TABLE(
  resource_id bigint,
  media_id bigint,
  platform_id text,
  title text,
  description text,
  cover_urls jsonb,
  author text,
  view_count bigint,
  created_at timestamptz,
  similarity double precision
)
LANGUAGE sql STABLE
SET search_path TO 'public', 'pg_catalog'
AS $$
  SELECT
    re.resource_id,
    pm.id                              AS media_id,
    pm.platform_id::text               AS platform_id,
    pm.title,
    pm.description,
    pm.cover_urls,
    pm.author::text                    AS author,
    COALESCE(pm.view_count, 0)::bigint AS view_count,
    pm.created_at,
    (1 - (re.embedding <=> query_embedding))::float AS similarity
  FROM resource_embeddings re
  JOIN resources r     ON r.id = re.resource_id
  JOIN parsed_media pm ON pm.id = r.media_id
  WHERE re.space_id = p_space_id
    AND re.layer = p_layer
    AND r.media_id IS NOT NULL
    AND r.is_trashed = false
    AND r.source_type = 'web'
    AND (p_user_id IS NULL OR r.creator_id = p_user_id)
    AND (1 - (re.embedding <=> query_embedding)) > match_threshold
  ORDER BY re.embedding <=> query_embedding
  LIMIT match_count;
$$;
REVOKE EXECUTE ON FUNCTION public.match_resource_embeddings(halfvec, bigint, text, double precision, integer, uuid)
  FROM PUBLIC, anon, authenticated;

-- find_duplicate_videos: same signature (CREATE OR REPLACE keeps the ACL that
-- 490/491 closed; REVOKEd again anyway so this file stands on its own), pairs
-- now come from resource_embeddings and must share a space.
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
        SELECT pm.id, re.embedding, re.space_id
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        JOIN resource_embeddings re ON re.resource_id = r.id AND re.layer = 'semantic'
        WHERE r.creator_id = p_user_id
          AND r.is_trashed = false
          AND pm.keep_forever = false
    ),
    similarity_pairs AS (
        SELECT
            um1.id AS mid1,
            um2.id AS mid2,
            (1 - (um1.embedding <=> um2.embedding))::float AS sim_score
        FROM user_media um1
        JOIN user_media um2 ON um2.id > um1.id AND um2.space_id = um1.space_id
        WHERE (1 - (um1.embedding <=> um2.embedding)) >= similarity_threshold
    )
    SELECT DISTINCT ON (sp.mid2)
        sp.mid2 AS media_id,
        pm.title,
        pm.cover_urls,
        pm.author,
        pm.storage_size,
        pm.created_at,
        pm.last_viewed_at,
        pm.view_count,
        sp.mid1 AS similar_to,
        sp.sim_score AS similarity_score
    FROM similarity_pairs sp
    JOIN parsed_media pm ON pm.id = sp.mid2
    ORDER BY sp.mid2, sp.sim_score DESC
    LIMIT max_results;
END;
$$;
REVOKE EXECUTE ON FUNCTION public.find_duplicate_videos(uuid, double precision, integer)
  FROM PUBLIC, anon, authenticated;

COMMIT;
