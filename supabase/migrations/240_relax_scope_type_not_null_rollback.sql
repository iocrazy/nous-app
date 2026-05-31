-- Rollback for migration 240 — re-impose NOT NULL on scope_type.
--
-- (CI auto-detect skips *_rollback.sql, so this never auto-applies; run it
--  manually if you need to revert migration 240.)
--
-- SET NOT NULL fails if any NULL rows exist. That is safe to run as-is *before*
-- the Phase 4b code PR stops writing scope_type (no NULLs yet). If NULLs already
-- exist, backfill from teams.kind first:
--
--   UPDATE public.resource_items ri
--     SET scope_type = CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END
--     FROM public.teams t
--     WHERE ri.scope_id::text = t.id::text AND ri.scope_type IS NULL;
--   UPDATE public.folders f
--     SET scope_type = CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END
--     FROM public.teams t
--     WHERE f.scope_id::text = t.id::text AND f.scope_type IS NULL;

ALTER TABLE public.resource_items ALTER COLUMN scope_type SET NOT NULL;
ALTER TABLE public.folders ALTER COLUMN scope_type SET NOT NULL;
