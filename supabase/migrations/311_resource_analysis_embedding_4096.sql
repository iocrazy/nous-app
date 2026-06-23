-- 311_resource_analysis_embedding_4096.sql
-- Switch resource_analysis.content_embedding to the platform qwen embedder's
-- dimension (4096). The prior vector(1536) sized for OpenAI
-- text-embedding-3-small is dead in prod (OPENAI_API_KEY unset) — 0 rows carry
-- an embedding, so this is a clean, lossless retype.
--
-- The ivfflat index is DROPPED, not rebuilt: pgvector ivfflat/hnsw cap at 2000
-- dimensions, so a 4096-dim vector cannot be indexed by them. At current scale a
-- sequential scan is fine. Scale path (when row count grows): convert the column
-- to halfvec(4096) and build an hnsw index (halfvec supports up to 4096 dims).

DROP INDEX IF EXISTS public.idx_resource_analysis_embedding;

ALTER TABLE public.resource_analysis
    ALTER COLUMN content_embedding TYPE vector(4096)
    USING NULL;  -- 0 rows populated; existing 1536-typed values (none) discarded

COMMENT ON COLUMN public.resource_analysis.content_embedding IS
    'pgvector(4096) — qwen3-embedding-8b. Unindexed (4096 > pgvector ivfflat/hnsw
     2000-dim cap); seq scan at current scale, halfvec+hnsw is the scale path.';
