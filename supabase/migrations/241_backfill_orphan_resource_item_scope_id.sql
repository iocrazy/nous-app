-- Migration 241: PR-E Phase 4c (prep) — backfill orphan resource_items.scope_id
--
-- 13 resource_items have scope_id set to a user's UUID instead of their
-- personal-team snowflake. These were written by the legacy parser/download
-- paths (media_service / downloader, which inserted scope_id = user_id) — those
-- writes were removed in Phase 4b. Post Spec-1 PR-C, scope_id must always be a
-- teams.id snowflake; with the wrong value these rows are effectively invisible
-- to their owner (the resource library UI queries by personal-team id), and they
-- would block the scope_id → teams(id) FK planned for a later Phase-4 step.
--
-- Remap each orphan to the owner's personal team (the user whose teams.owner_id
-- equals the stored UUID and whose team.kind = 'personal').
--
-- Verified on prod 2026-05-30 via BEGIN/ROLLBACK: orphans 13 → 0, exactly 13
-- rows updated. folders / tags / smart_collections had 0 orphans.

UPDATE public.resource_items ri
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE t.owner_id::text = ri.scope_id
   AND t.kind = 'personal'
   AND NOT EXISTS (
     SELECT 1 FROM public.teams t2 WHERE t2.id::text = ri.scope_id
   );
