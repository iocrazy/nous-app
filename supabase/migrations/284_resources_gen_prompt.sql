-- 284: resources.gen_prompt — AI generation prompt attached to an asset.
--
-- User-entered (ResourceDetailPage Overview prompt block) or, later,
-- auto-extracted (PNG ComfyUI/A1111 metadata, canvas derive chain).
-- Plain TEXT on purpose: negative prompt / seed / model can move to a
-- jsonb sibling if and when they're actually needed.
--
-- RLS: row-level isolation on resources already governs visibility;
-- a new column inherits it. No policy changes needed.

ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS gen_prompt TEXT;

COMMENT ON COLUMN public.resources.gen_prompt IS
    'AI generation prompt for this asset (user-entered or auto-extracted)';

-- PostgREST must reload its schema cache to see the new column
-- (see bug_ci_migration_skips_postgrest_reload — the CI runner cannot
-- restart the REST container, but NOTIFY from inside psql works).
NOTIFY pgrst, 'reload schema';
