-- 507: video_shots / video_shot_embeddings / video_shot_indexes — the shot
-- index behind the Visual retrieval layer (spec
-- docs/superpowers/specs/2026-09-25-pr3-shots-index-design.md §3).
--
-- Why three tables
-- ================
-- * video_shots is the cut list of one video: one row per shot with the
--   millisecond span and the frame that represents it. Deleting the resource
--   deletes the list (CASCADE); re-indexing replaces it wholesale.
-- * video_shot_embeddings holds one halfvec(2048) per (shot, kind, space).
--   kind='frame' is the representative frame embedded as an image (this PR);
--   kind='clip' is reserved for the PR 4 clip vector. space_id follows 499:
--   a vector is only comparable inside its embedding space, and switching the
--   model (#2433) means a new space + re-embed, never a cross-space compare.
-- * video_shot_indexes is the fact "this video has been cut", independent of
--   vectors: a black / audio-only video cuts to zero shots and is still
--   indexed. Coverage of the Visual layer counts frame vectors in the CURRENT
--   space; this row tells "cut with which algorithm, when, how many".
--
-- halfvec, not vector: HNSW on vector caps at 2000 dims (499 has the story).
-- No cluster_id column yet: PR 4 adds it when there is a writer.

CREATE TABLE IF NOT EXISTS public.video_shots (
  id            bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  resource_id   bigint NOT NULL REFERENCES public.resources(id) ON DELETE CASCADE,
  shot_index    integer NOT NULL,
  start_ms      integer NOT NULL,
  end_ms        integer NOT NULL,
  rep_frame_ms  integer NOT NULL,
  cut_score     real,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT video_shots_resource_shot_key UNIQUE (resource_id, shot_index),
  CONSTRAINT video_shots_span_check CHECK (start_ms >= 0 AND end_ms > start_ms),
  CONSTRAINT video_shots_rep_frame_check CHECK (rep_frame_ms >= start_ms AND rep_frame_ms < end_ms)
);
CREATE INDEX IF NOT EXISTS video_shots_resource_idx ON public.video_shots (resource_id);
COMMENT ON TABLE public.video_shots IS
  'Cut list of one video: one row per shot (ms span + representative frame). Replaced wholesale on re-index.';

CREATE TABLE IF NOT EXISTS public.video_shot_embeddings (
  shot_id      bigint NOT NULL REFERENCES public.video_shots(id) ON DELETE CASCADE,
  kind         text NOT NULL,
  space_id     bigint NOT NULL REFERENCES public.embedding_spaces(id) ON DELETE CASCADE,
  embedding    halfvec(2048) NOT NULL,
  source_hash  text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT video_shot_embeddings_pkey PRIMARY KEY (shot_id, kind, space_id),
  CONSTRAINT video_shot_embeddings_kind_check CHECK (kind IN ('frame', 'clip'))
);
CREATE INDEX IF NOT EXISTS video_shot_embeddings_embedding_hnsw
  ON public.video_shot_embeddings USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS video_shot_embeddings_space_kind_idx
  ON public.video_shot_embeddings (space_id, kind);
COMMENT ON COLUMN public.video_shot_embeddings.source_hash IS
  '"<algo_version>:<sha1 of the representative frame bytes>": a re-cut or a re-extracted frame changes it.';

CREATE TABLE IF NOT EXISTS public.video_shot_indexes (
  resource_id   bigint PRIMARY KEY REFERENCES public.resources(id) ON DELETE CASCADE,
  algo_version  text NOT NULL,
  shot_count    integer NOT NULL DEFAULT 0,
  duration_ms   integer,
  indexed_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.video_shot_indexes IS
  'The fact that a video has been cut (which algorithm, when, how many shots) — independent of which spaces hold its vectors.';

-- Backend-only tables (direct SQL session). Same two layers as 499: the
-- browser roles hold no privileges at all, and RLS on top scopes reads to the
-- owner of the resource should a grant ever reappear.
ALTER TABLE public.video_shots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.video_shot_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.video_shot_indexes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Read shots of owned resources" ON public.video_shots;
CREATE POLICY "Read shots of owned resources" ON public.video_shots
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.resources r
    WHERE r.id = video_shots.resource_id AND r.creator_id = auth.uid()));
DROP POLICY IF EXISTS "Read shot embeddings of owned resources" ON public.video_shot_embeddings;
CREATE POLICY "Read shot embeddings of owned resources" ON public.video_shot_embeddings
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.video_shots s JOIN public.resources r ON r.id = s.resource_id
    WHERE s.id = video_shot_embeddings.shot_id AND r.creator_id = auth.uid()));
DROP POLICY IF EXISTS "Read shot indexes of owned resources" ON public.video_shot_indexes;
CREATE POLICY "Read shot indexes of owned resources" ON public.video_shot_indexes
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.resources r
    WHERE r.id = video_shot_indexes.resource_id AND r.creator_id = auth.uid()));
REVOKE ALL ON TABLE public.video_shots, public.video_shot_embeddings, public.video_shot_indexes
  FROM anon, authenticated;

-- Best shot per video for a query vector, in ONE space and ONE kind.
--
-- Unlike match_resource_embeddings a resource has many rows here, so the
-- page is DISTINCT ON (resource_id). The HNSW walk is asked for
-- match_count * 4 nearest shots (iterative_scan keeps walking until that many
-- pass the filters), the best shot of each resource is kept, and the survivors
-- are re-sorted by exact distance and cut to match_count. Videos whose four
-- nearest shots are all beaten by other videos' shots fall off — that is the
-- trade for one graph walk instead of one per resource.
--
-- Backend-only: REVOKEd from the browser roles (p_user_id NULL = every user).
DROP FUNCTION IF EXISTS public.match_video_shot_embeddings(halfvec, bigint, text, double precision, integer, uuid);
CREATE FUNCTION public.match_video_shot_embeddings(
  query_embedding halfvec,
  p_space_id      bigint,
  p_kind          text,
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
  shot_id bigint,
  shot_index integer,
  start_ms integer,
  end_ms integer,
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
      s.resource_id,
      s.id                               AS shot_id,
      s.shot_index,
      s.start_ms,
      s.end_ms,
      (se.embedding <=> query_embedding)::float AS distance
    FROM video_shot_embeddings se
    JOIN video_shots s   ON s.id = se.shot_id
    JOIN resources r     ON r.id = s.resource_id
    WHERE se.space_id = p_space_id
      AND se.kind = p_kind
      AND r.media_id IS NOT NULL
      AND r.is_trashed = false
      AND r.source_type = 'web'
      AND (p_user_id IS NULL OR r.creator_id = p_user_id)
      AND (1 - (se.embedding <=> query_embedding)) > match_threshold
    ORDER BY se.embedding <=> query_embedding
    LIMIT match_count * 4
  ),
  best AS (
    SELECT DISTINCT ON (h.resource_id) h.*
    FROM hits h
    ORDER BY h.resource_id, h.distance
  )
  SELECT b.resource_id,
         pm.id                              AS media_id,
         pm.platform_id::text               AS platform_id,
         pm.title,
         pm.description,
         pm.cover_urls,
         pm.author::text                    AS author,
         COALESCE(pm.view_count, 0)::bigint AS view_count,
         pm.created_at,
         b.shot_id, b.shot_index, b.start_ms, b.end_ms,
         (1 - b.distance)::float            AS similarity
  FROM best b
  JOIN resources r     ON r.id = b.resource_id
  JOIN parsed_media pm ON pm.id = r.media_id
  ORDER BY b.distance + 0
  LIMIT match_count;
END;
$$;
REVOKE EXECUTE ON FUNCTION public.match_video_shot_embeddings(halfvec, bigint, text, double precision, integer, uuid)
  FROM PUBLIC, anon, authenticated;
