-- 300 — pg_trgm GIN index on resources.filename for fast keyword search.
--
-- The resources library keyword search filters `resources.filename ILIKE
-- '%q%'` (server-side, across the whole flattened scope). A leading-wildcard
-- ILIKE cannot use a btree index, so without a trigram index this is a
-- sequential scan of `resources` on every keystroke — slow once a scope grows.
--
-- `resources.notes` already has a trigram index (mig 154) and `tags.name`
-- (mig 154) too; filename was the missing piece. This turns the filename
-- ILIKE into a Bitmap Index Scan so search stays fast at scale.
--
-- pg_trgm is already enabled (migs 024 / 153 / 154 use gin_trgm_ops). The
-- CREATE EXTENSION guard is kept for fresh-environment provisioning.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_resources_filename_trgm
  ON resources USING gin (filename gin_trgm_ops);
