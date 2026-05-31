-- Migration 245: backfill resource_items orphaned by the PR-E 4c-3 bigint switch
--
-- The media parser/downloader paths (media_service / downloader) were still
-- inserting resource_items.scope_id = user_id (a UUID) — Phase 4b's claim that
-- those writes were removed was incomplete. While scope_id was a text column
-- this merely produced semantically-wrong rows (cleaned by migration 241).
-- After PR-E 4c-3 (migration 244) made scope_id a bigint, the raw-UUID insert
-- fails with 22P02 and is swallowed by the caller's try/except — so the resource
-- row gets created but its personal-scope resource_item never does, leaving the
-- downloaded media invisible in the owner's library (same failure class as the
-- 13 orphans repaired by migration 241, now re-appearing as MISSING rows rather
-- than mis-scoped ones).
--
-- The code fix (resolve user_id -> personal-team snowflake before the insert)
-- stops new orphans. This migration backfills the ones already created in the
-- gap: for every non-trashed resource whose creator has a personal team but no
-- resource_item, insert the missing personal-scope item.
--
-- Verified on prod 2026-05-31 via BEGIN/ROLLBACK: exactly 3 rows inserted,
-- no FK/constraint violations.

INSERT INTO public.resource_items (resource_id, scope_id, added_by)
SELECT r.id, t.id, r.creator_id
  FROM public.resources r
  JOIN public.teams t
    ON t.owner_id = r.creator_id
   AND t.kind = 'personal'
 WHERE r.is_trashed = false
   AND NOT EXISTS (
     SELECT 1 FROM public.resource_items ri WHERE ri.resource_id = r.id
   );
