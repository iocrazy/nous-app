-- Rollback for migration 243. (CI auto-detect skips *_rollback.sql.)
--
-- ⚠️ 243 is IRREVERSIBLE for the original raw values, BUT scope_type and
-- is_personal are both pure derivations of teams.kind, so the columns can be
-- fully reconstructed without data loss. (The pre-243 pg_dump is the ultimate
-- fallback: /volume1/docker/datahub/mediahub-sb-prod/pre_4c2b_20260530_091544.sql)
--
-- This recreates the columns and rebuilds their values from teams.kind. The
-- teams_sync_is_personal trigger/function are NOT recreated here — restore them
-- from migrations 236 / 238 if needed.

-- Recreate scope_type columns (nullable; original CHECK was IN('personal','team')).
ALTER TABLE public.resource_items    ADD COLUMN IF NOT EXISTS scope_type text;
ALTER TABLE public.folders           ADD COLUMN IF NOT EXISTS scope_type text;
ALTER TABLE public.tags              ADD COLUMN IF NOT EXISTS scope_type text;
ALTER TABLE public.smart_collections ADD COLUMN IF NOT EXISTS scope_type text;
ALTER TABLE public.teams             ADD COLUMN IF NOT EXISTS is_personal boolean NOT NULL DEFAULT false;

-- Rebuild values from teams.kind.
UPDATE public.resource_items ri
   SET scope_type = CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END
  FROM public.teams t WHERE t.id::text = ri.scope_id::text;
UPDATE public.folders f
   SET scope_type = CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END
  FROM public.teams t WHERE t.id::text = f.scope_id::text;
UPDATE public.tags g
   SET scope_type = CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END
  FROM public.teams t WHERE t.id::text = g.scope_id::text;
UPDATE public.smart_collections s
   SET scope_type = CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END
  FROM public.teams t WHERE t.id::text = s.scope_id::text;
UPDATE public.teams SET is_personal = (kind = 'personal');

-- Drop the scope_id-only indexes added by 243 (the originals can be restored
-- from migration 051 if the composite form is wanted back).
DROP INDEX IF EXISTS public.idx_folders_scope_id;
DROP INDEX IF EXISTS public.idx_folders_trashed_scope_id;
DROP INDEX IF EXISTS public.idx_resource_items_scope_id;
DROP INDEX IF EXISTS public.idx_smart_collections_scope_id;
