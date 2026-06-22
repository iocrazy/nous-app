-- 308_hotspot_heat.sql
-- Objective heat signal for hotspots. Until now rank_timeline was never
-- written (ingest used upsert ignore-duplicates) and ranking relied solely on
-- the LLM's subjective score. This adds a computed `heat` (0..1) derived from
-- board rank + persistence + recency, populated at ingest.

ALTER TABLE hotspots ADD COLUMN IF NOT EXISTS heat NUMERIC;

-- Heat-ordered browsing of the most popular items.
CREATE INDEX IF NOT EXISTS idx_hotspots_heat ON hotspots (heat DESC NULLS LAST);

-- PostgREST: reload schema cache so the new column is visible.
NOTIFY pgrst, 'reload schema';
