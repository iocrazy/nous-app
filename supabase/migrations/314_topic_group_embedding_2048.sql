-- 314_topic_group_embedding_2048.sql
-- Align topic_groups.embedding to the hotspot embedding dimension (2048,
-- Volcengine doubao-embedding-vision). The mig304 vector(1536) sizing was for
-- OpenAI text-embedding-3-small and is dead (0 group rows). A group's embedding
-- is the cluster centroid (seeded from its first member) used to match new
-- cross-source hotspots by cosine similarity.
--
-- Unindexed: 2048 > pgvector ivfflat/hnsw 2000-dim cap; seq cosine scan over the
-- recent-window group set is fine at scale (tens–hundreds of active groups).

DROP INDEX IF EXISTS public.idx_topic_groups_embedding;

ALTER TABLE public.topic_groups
    ALTER COLUMN embedding TYPE vector(2048)
    USING NULL;  -- 0 rows populated; the prior 1536-typed values (none) discarded

COMMENT ON COLUMN public.topic_groups.embedding IS
    'pgvector(2048) cluster centroid (seed = first member). Unindexed; seq cosine
     scan over the active-window groups. Matches hotspots.embedding dimension.';

NOTIFY pgrst, 'reload schema';
