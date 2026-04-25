-- 153 — pg_trgm GIN indexes for fast ILIKE on parsed_media text columns
--
-- Context: ``POST /api/v1/search/text`` runs ``ILIKE '%query%'`` against
-- ``title / description / author / hashtags``. Without indexes this is a
-- sequential scan (O(N) per row). On libraries past ~5k rows the latency
-- starts being user-visible. Trigram GIN indexes make ILIKE planner-aware
-- so the same query becomes a Bitmap Index Scan (5-30 ms regardless of
-- table size up to ~1M rows).
--
-- Applied directly via Supabase MCP on 2026-04-25. This file exists as a
-- record for fresh environment provisioning.
--
-- Verification:
--   EXPLAIN ANALYZE SELECT * FROM parsed_media
--   WHERE title ILIKE '%memory%' OR description ILIKE '%memory%';
-- Expect: "Bitmap Heap Scan ... Recheck Cond: ..." with the trgm indexes
-- listed under "Bitmap Index Scan".

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_parsed_media_title_trgm
  ON parsed_media USING gin (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_parsed_media_description_trgm
  ON parsed_media USING gin (description gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_parsed_media_author_trgm
  ON parsed_media USING gin (author gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_parsed_media_hashtags_trgm
  ON parsed_media USING gin (hashtags gin_trgm_ops);
