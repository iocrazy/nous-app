-- 456_generated_media_promoted_unique.sql
--
-- One inbox row per promoted resource. insert_registered_resource's
-- find-or-mint is user-triggered and concurrently reachable; the advisory
-- lock it takes serialises callers so the loser FINDS the winner's row, and
-- this index is the guarantee behind it: a second row for the same resource
-- can no longer exist even if a future caller forgets the lock. Checked on
-- prod 2026-09-06 before writing: 0 duplicates among 29 promoted rows.
-- idx_genmedia_promoted (plain btree) is subsumed and dropped.
CREATE UNIQUE INDEX IF NOT EXISTS uq_genmedia_promoted_resource
  ON public.generated_media (promoted_resource_id)
  WHERE promoted_resource_id IS NOT NULL;
DROP INDEX IF EXISTS public.idx_genmedia_promoted;
