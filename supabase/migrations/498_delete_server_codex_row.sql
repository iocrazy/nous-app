-- 498: retire the server-side Codex line — delete its catalog row.
--
-- The `codex` protocol (ChatGPT subscription session bind-mounted into the
-- containers, driven by gpt-image-2-skill) is removed from code in the same
-- PR, together with the admin Codex auth panel and its status routes. What
-- stays: `codex-local` (the user's own paired daemon) and `openai-images`
-- (API key, same CLI binary, `--provider openai`).
--
-- Production shape (2026-09-23): exactly one row, `codex-image`, already
-- disabled by 484. Every catalog consumer filters on is_enabled, so deleting
-- it changes no runtime behaviour; it only removes a row whose protocol no
-- longer exists (an enabled row with an unknown actual_provider would make
-- resolve_generation_protocol return None).
--
-- Delete, not disable (unlike 484/494): the protocol is gone, so there is no
-- switch left to flip back. Recreate from 430 if ever needed.
--
-- References by NAME are cleaned the way 488 cleaned renamed rows. No table
-- has an FK to nous_models, so name references are the only kind:
--   a. system_settings model keys holding 'nous:codex-image' → key deleted
--      (falls back to the default, same as a missing key today)
--   b. user_settings ai_settings.task_assignment.* == 'nous:codex-image'
--      → that assignment removed (falls back to the default)
--   c. user_settings ai_settings.ai_providers.nous.disabled_models[] entries
--      'codex-image' → dropped (hiding a row that no longer exists)
-- Each step is guarded on the reference still being there, so a re-run
-- reports 0 everywhere; safe on an empty DB.

DO $$
DECLARE
    n INT;
BEGIN
    -- a. system_settings
    DELETE FROM public.system_settings s
     WHERE (   s.key IN ('maintenance_llm_model', 'graph_embedder_model', 'graph_extractor_model')
            OR (left(s.key, 10) = 'ai_module.' AND right(s.key, 6) = '.model'))
       AND jsonb_typeof(s.value) = 'string'
       AND s.value #>> '{}' IN ('nous:codex-image', 'codex-image');
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '[498] system_settings references removed: %', n;

    -- b. user_settings task_assignment
    UPDATE public.user_settings us
       SET settings_json = jsonb_set(
               us.settings_json,
               '{ai_settings,task_assignment}',
               COALESCE(
                   (SELECT jsonb_object_agg(e.key, e.value)
                      FROM jsonb_each(us.settings_json #> '{ai_settings,task_assignment}') e
                     WHERE NOT (jsonb_typeof(e.value) = 'string'
                                AND e.value #>> '{}' = 'nous:codex-image')),
                   '{}'::jsonb))
     WHERE jsonb_typeof(us.settings_json #> '{ai_settings,task_assignment}') = 'object'
       AND EXISTS (
           SELECT 1
             FROM jsonb_each(us.settings_json #> '{ai_settings,task_assignment}') e
            WHERE jsonb_typeof(e.value) = 'string'
              AND e.value #>> '{}' = 'nous:codex-image');
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '[498] user task_assignment rows cleaned: %', n;

    -- c. user_settings disabled_models[] (order preserved)
    UPDATE public.user_settings us
       SET settings_json = jsonb_set(
               us.settings_json,
               '{ai_settings,ai_providers,nous,disabled_models}',
               COALESCE(
                   (SELECT jsonb_agg(el.value ORDER BY el.ord)
                      FROM jsonb_array_elements(
                               us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}'
                           ) WITH ORDINALITY AS el(value, ord)
                     WHERE NOT (jsonb_typeof(el.value) = 'string'
                                AND el.value #>> '{}' = 'codex-image')),
                   '[]'::jsonb))
     WHERE jsonb_typeof(us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}') = 'array'
       AND us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}' ? 'codex-image';
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '[498] user disabled_models rows cleaned: %', n;

    -- The row itself.
    DELETE FROM public.nous_models WHERE actual_provider = 'codex';
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '[498] server codex catalog rows deleted: %', n;
END $$;
