-- 313_hotspot_embedding.sql
-- Embedding column for hotspots, for cross-source topic clustering (same topic
-- across weibo/zhihu/36kr → one group + "seen on N platforms" signal).
--
-- Dimension 2048 = Volcengine Ark doubao-embedding-vision-250615 (the activated
-- model in prod). Unindexed: pgvector ivfflat/hnsw cap at 2000 dims, so 2048
-- cannot be indexed by them — a sequential cosine scan over the recent window is
-- fine at hotspot scale (hundreds/day). Scale path: halfvec(2048) + hnsw.
--
-- The embedding provider is admin-governed (ai_module.embedding.*), NEVER env.

ALTER TABLE public.hotspots ADD COLUMN IF NOT EXISTS embedding vector(2048);

COMMENT ON COLUMN public.hotspots.embedding IS
    'pgvector(2048) — Volcengine doubao-embedding-vision. Unindexed (2048 >
     pgvector ivfflat/hnsw 2000-dim cap); seq cosine scan at current scale.';

NOTIFY pgrst, 'reload schema';
