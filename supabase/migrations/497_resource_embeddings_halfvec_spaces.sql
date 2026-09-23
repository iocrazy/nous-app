-- 497: resource_embeddings (halfvec 2048 + HNSW) + embedding_spaces.
--
-- resource_analysis.content_embedding is vector(2048): pgvector's HNSW caps
-- `vector` at 2000 dims, so that column has never had an index and every
-- semantic query is a sequential scan. halfvec goes to 4000, so the vectors
-- move to a table of their own, keyed by (resource, layer, space):
--   * layer    — which retrieval layer ('semantic' now; 'transcript' later)
--   * space_id — which embedder produced it (embedding_spaces). A vector only
--     means something next to vectors of the same space; switching models is
--     "new space + re-embed", never "overwrite in place".
-- The old column stays for one release, but readers only fall back to it
-- narrowly: nearest-neighbour search reads the old column only when this
-- table/function is missing (store_missing, i.e. code deployed ahead of the
-- migration); find_similar falls back per source resource that has no row
-- here yet. A half-filled table is NOT merged with the old column. mig 490's
-- embedding_model stamp on resource_analysis is superseded for this table by
-- space_id and left in place.
--
-- Requires pgvector >= 0.8 (hnsw.iterative_scan). CI runs
-- pgvector/pgvector:pg17 and production supabase/postgres:17.6.1.084, both
-- 0.8.x (drift DB measured 0.8.5 on 2026-09-23).
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
-- source_hash today only matters in two places: analyze_l1 re-running
-- (upsert overwrites the row) and idempotence within one backfill request.
-- The backfill selects resources with NO row, so a changed hash (document
-- composer version bump) does not by itself trigger a re-embed yet — see
-- spec 2026-09-16-video-vector-layers-design.md §9.
COMMENT ON COLUMN public.resource_embeddings.source_hash IS
  'sha1 of the embedded document (+ composer version). Not yet used to trigger re-embeds; see spec §9.';

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
-- The policies are a second layer; the first is that the browser roles hold
-- no privileges on these tables at all (Supabase default privileges grant
-- anon/authenticated ALL on new public tables).
REVOKE ALL ON TABLE public.embedding_spaces, public.resource_embeddings
  FROM anon, authenticated;

-- Nearest resources in ONE space and ONE layer. One row per resource by
-- construction (PK), and resources <-> parsed_media is 1:1, so no DISTINCT ON:
-- ORDER BY distance LIMIT n is exactly the shape the HNSW index serves.
--
-- HNSW returns only hnsw.ef_search (default 40) global neighbours and the
-- space / layer / user / threshold filters run AFTER that, so on a multi-user
-- library a caller can get a short or empty page while vector_leg still reads
-- ok. iterative_scan = relaxed_order keeps walking the graph until LIMIT is
-- satisfied (pgvector >= 0.8); relaxed order means the index output can be
-- slightly out of order, so the hits are re-sorted by exact distance. The
-- SETs live on the function so they apply however it is called.
--
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
LANGUAGE plpgsql STABLE
SET search_path TO 'public', 'pg_catalog'
SET hnsw.iterative_scan = 'relaxed_order'
SET hnsw.ef_search = 100
AS $$
#variable_conflict use_column
BEGIN
  RETURN QUERY
  WITH hits AS MATERIALIZED (
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
      (re.embedding <=> query_embedding)::float AS distance
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
    LIMIT match_count
  )
  SELECT h.resource_id, h.media_id, h.platform_id, h.title, h.description,
         h.cover_urls, h.author, h.view_count, h.created_at,
         (1 - h.distance)::float AS similarity
  FROM hits h
  -- "+ 0": PG17 carries the CTE's (index) sort order out of the CTE and
  -- would skip this sort; relaxed_order output is only approximately sorted.
  -- This is pgvector's documented re-sort idiom.
  ORDER BY h.distance + 0;
END;
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
