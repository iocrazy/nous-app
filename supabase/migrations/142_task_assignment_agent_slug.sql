-- Migration 142: rewrite task_assignment values to agent slugs
-- Phase 2 PR 2.8b — switch task_assignment from "provider:model" format
-- to AI Library agent slugs. Establishes a single source of truth.
--
-- Before (legacy):
--   task_assignment.summarization   = "openai:gpt-4o-mini"
--   task_assignment.visual_analysis = "doubao:doubao-seed-2-0-pro-260215"
--   task_assignment.script_generation = "doubao:doubao-seed-2-0-lite-260215"
--
-- After (agent slug):
--   task_assignment.summarization     = "summarize"
--   task_assignment.visual_analysis   = "analyze"
--   task_assignment.script_generation = "storyboard"
--
-- task_assignment.transcription is NOT touched — transcription is a
-- Whisper/Volcengine ASR workflow that does not route through the agent
-- framework, and continues to use "provider:model" strings.
--
-- Note: the agent slugs referenced here (summarize / analyze / storyboard)
-- are seeded as system presets in migration 138 + backend/seeds/agents/.

UPDATE public.user_settings
SET settings_json = jsonb_set(
        jsonb_set(
            jsonb_set(
                settings_json,
                '{ai_settings,task_assignment,summarization}',
                '"summarize"'::jsonb,
                true
            ),
            '{ai_settings,task_assignment,visual_analysis}',
            '"analyze"'::jsonb,
            true
        ),
        '{ai_settings,task_assignment,script_generation}',
        '"storyboard"'::jsonb,
        true
    )
WHERE settings_json ? 'ai_settings'
  AND (settings_json->'ai_settings') ? 'task_assignment';
