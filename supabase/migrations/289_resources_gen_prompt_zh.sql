-- 289: resources.gen_prompt_zh — Chinese counterpart of gen_prompt.
--
-- The asset prompt block is bilingual (EN/ZH toggle in the UI). gen_prompt
-- stays the canonical English/source-language text (PNG metadata extraction
-- writes here — A1111/ComfyUI prompts are English); gen_prompt_zh holds the
-- Chinese side. Either side can be user-entered or filled by the provider-
-- routed `translate` agent (task_assignment.translation).
--
-- RLS: row-level isolation on resources already governs visibility;
-- a new column inherits it. No policy changes needed.

ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS gen_prompt_zh TEXT;

COMMENT ON COLUMN public.resources.gen_prompt_zh IS
    'Chinese-language AI generation prompt (user-entered or provider-translated)';

-- PostgREST must reload its schema cache to see the new column
-- (see bug_ci_migration_skips_postgrest_reload — the CI runner cannot
-- restart the REST container, but NOTIFY from inside psql works).
NOTIFY pgrst, 'reload schema';
