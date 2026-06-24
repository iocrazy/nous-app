-- 315_resource_analysis_embedding_2048.sql
-- Align resource_analysis.content_embedding to the active platform embedder
-- dimension (2048, Volcengine doubao-embedding-vision), matching topic_groups
-- (mig 314) + the hotspot embedding. Migration 311 had sized it 4096 for the
-- now-retired self-hosted qwen embedder; 0 rows carry an embedding, so this is a
-- clean, lossless retype.
--
-- Unindexed: 2048 > pgvector ivfflat/hnsw 2000-dim cap, so a vector index is not
-- buildable; a sequential cosine scan is correct at current scale (same call as
-- mig 314). match_videos_by_embedding(query_embedding vector,...) is untyped →
-- no RPC change.

DROP INDEX IF EXISTS public.idx_resource_analysis_embedding;

ALTER TABLE public.resource_analysis
    ALTER COLUMN content_embedding TYPE vector(2048)
    USING NULL;  -- 0 rows populated; clean retype

COMMENT ON COLUMN public.resource_analysis.content_embedding IS
    'pgvector(2048) — doubao-embedding-vision (platform embedder). Unindexed
     (2048 > pgvector ivfflat/hnsw 2000-dim cap); seq cosine scan at this scale.';
