-- 252_backfill_resources_resolution_from_parsed_media.sql
--
-- Downloads recorded resolution only on parsed_media; resources.resolution was
-- never mirrored from it. The download *detail* page reads parsed_media so it
-- looked fine there, but the resource library grid / Justified view reads
-- resources.resolution and fell back to a 1:1 aspect for every downloaded item.
--
-- Backfill resources.resolution from the canonical parsed_media row via
-- resources.media_id. Covers downloaded videos (parser always has resolution)
-- and any downloaded image whose parsed_media row carries dimensions.
--
-- Idempotent: only touches rows where resources.resolution IS NULL and the
-- source resolution exists. Safe to re-run. Pure DML — no schema change, so no
-- PostgREST reload needed.

UPDATE resources r
SET resolution = pm.resolution
FROM parsed_media pm
WHERE r.media_id = pm.id
  AND r.resolution IS NULL
  AND pm.resolution IS NOT NULL
  AND pm.resolution <> '';
