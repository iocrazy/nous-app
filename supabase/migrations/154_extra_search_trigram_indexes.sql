-- 154 — pg_trgm GIN indexes for the new search scopes added in Eagle-style
-- scope picker (tags / notes / transcript).
--
-- - ``parsed_media.ai_extract_text`` (transcripts, can be 10k+ chars per row)
-- - ``resources.notes`` (per-user user notes)
-- - ``tags.name`` (used to look up tag IDs that match the substring before
--   joining back through ``resource_tags``)
--
-- Without indexes, ILIKE on these columns is a sequential scan. Trigram
-- GIN turns each into a Bitmap Index Scan. ``ai_extract_text`` is the
-- biggest win — those rows can be very large, so seq-scanning every one
-- to find a substring is cripplingly slow on libraries with AI completions.
--
-- Applied directly via Supabase MCP on 2026-04-25. This file exists as a
-- record for fresh environment provisioning.

CREATE INDEX IF NOT EXISTS idx_parsed_media_ai_extract_text_trgm
  ON parsed_media USING gin (ai_extract_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_resources_notes_trgm
  ON resources USING gin (notes gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_tags_name_trgm
  ON tags USING gin (name gin_trgm_ops);
