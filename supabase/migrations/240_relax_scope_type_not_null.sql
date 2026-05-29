-- Migration 240: PR-E Phase 4a (prep) — relax scope_type NOT NULL
--
-- Spec 1 PR-E removes the vestigial `scope_type` column. The final DROP is a
-- later migration (Phase 4b); it can only run after the app stops writing the
-- column. Today the app still writes it on INSERT:
--   * frontend resourceService.createFolder / copyResourceItem (direct PostgREST)
--   * backend _scope_type_for() on the resources/folders/smart-folder INSERT paths
-- Those writes can only be removed once the column tolerates absence.
--
-- resource_items.scope_type and folders.scope_type are NOT NULL with no default
-- (migration 044). This relaxes them to nullable so the follow-up code PR
-- (Phase 4b) can stop populating them. The CHECK (scope_type IN
-- ('personal','team')) is intentionally left in place — a CHECK passes for NULL
-- (evaluates to UNKNOWN), so it does not block NULL inserts and needs no change.
--
-- tags.scope_type / smart_collections.scope_type are already nullable with a
-- DEFAULT 'personal' (migration 045), so they need nothing here.
--
-- Fully backward-compatible and reversible: the column still exists, every RLS
-- policy that references it keeps working, and the currently-deployed app keeps
-- writing 'personal'/'team' exactly as before. Nothing changes behaviourally;
-- this only widens what the column will accept.

ALTER TABLE public.resource_items ALTER COLUMN scope_type DROP NOT NULL;
ALTER TABLE public.folders ALTER COLUMN scope_type DROP NOT NULL;
