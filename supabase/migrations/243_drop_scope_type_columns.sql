-- Migration 243: PR-E Phase 4c-2b — DROP the scope_type / is_personal columns
--
-- ⚠️ IRREVERSIBLE (drops production columns). Prerequisites, all DONE before this:
--   * 4b (#377): app stopped WRITING scope_type
--   * 4c-1a (#378): orphan resource_items.scope_id backfilled (orphans 0)
--   * 4c-1b (#379, mig 242): all RLS policies rewritten off scope_type (0 refs)
--   * 4c-2a (#380): app stopped READING scope_type (deployed)
-- A pg_dump of all 5 affected tables was taken on prod first:
--   /volume1/docker/datahub/mediahub-sb-prod/pre_4c2b_20260530_091544.sql
-- Dry-run verified on prod 2026-05-30 (BEGIN/ROLLBACK, ON_ERROR_STOP=1): all
-- DROPs + index rebuilds succeed; afterwards 0 scope_type columns remain and
-- teams.is_personal is gone.
--
-- scope_type is fully redundant with teams.kind (scope_id is always a teams.id
-- snowflake post PR-C); teams.is_personal is redundant with teams.kind too.

-- 1. teams.is_personal sync trigger + function (must go before the column).
DROP TRIGGER IF EXISTS teams_sync_is_personal_trg ON public.teams;
DROP FUNCTION IF EXISTS public.teams_sync_is_personal();

-- 2. Drop the scope_type columns (cascades the CHECK constraints and the
--    composite (scope_type, scope_id) indexes that include the column).
ALTER TABLE public.resource_items    DROP COLUMN scope_type;
ALTER TABLE public.folders           DROP COLUMN scope_type;
ALTER TABLE public.tags              DROP COLUMN scope_type;
ALTER TABLE public.smart_collections DROP COLUMN scope_type;

-- 3. Drop teams.is_personal (redundant with teams.kind).
ALTER TABLE public.teams DROP COLUMN is_personal;

-- 4. Recreate the scope lookup indexes as single-column scope_id indexes
--    (the old (scope_type, scope_id) composites were dropped with the column;
--    all current queries filter by scope_id alone).
CREATE INDEX IF NOT EXISTS idx_folders_scope_id
  ON public.folders(scope_id);
CREATE INDEX IF NOT EXISTS idx_folders_trashed_scope_id
  ON public.folders(scope_id, is_trashed) WHERE is_trashed = true;
CREATE INDEX IF NOT EXISTS idx_resource_items_scope_id
  ON public.resource_items(scope_id);
CREATE INDEX IF NOT EXISTS idx_smart_collections_scope_id
  ON public.smart_collections(scope_id);
