-- Topic groups: persist the DISTINCT member source labels (platform names)
-- alongside source_count, so the feed can show WHICH sources a cross-source
-- topic appears on (the hover tooltip on "N sources"), not just how many.
--
-- Why persist instead of join-at-read: source_count is already maintained on
-- the group by the clusterer (recompute_group); the member labels are the same
-- aggregate over the same members. Persisting them keeps the read path a plain
-- PostgREST embed (topic_groups(source_count, source_labels)) with no fragile
-- self-referential hotspots→topic_groups→hotspots embed.

ALTER TABLE topic_groups
  ADD COLUMN IF NOT EXISTS source_labels text[] NOT NULL DEFAULT '{}';

-- Backfill existing groups from their current members so the tooltip works for
-- topics clustered before this migration (not only ones recomputed after).
UPDATE topic_groups g SET source_labels = COALESCE((
  SELECT array_agg(DISTINCT h.source_label ORDER BY h.source_label)
    FROM hotspots h
   WHERE h.topic_group_id = g.id
     AND h.source_label IS NOT NULL
     AND h.source_label <> ''
), '{}');

-- PostgREST must re-read the schema to expose the new column on the embed.
NOTIFY pgrst, 'reload schema';
