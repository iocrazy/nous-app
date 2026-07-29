-- 393: Point the built-in caption / classify agents at a working vision model.
--
-- Background
-- ----------
-- Both system-preset agents shipped with model 'Qwen/Qwen3-VL-235B-A22B-Instruct'.
-- That id is NOT in the platform catalog (`mediahub_models`), so
-- resolve_task_ai_config falls through to the BYOK path → provider key derived as
-- modelscope → the stored ModelScope key is no longer valid → every Generate
-- (image → prompt) and Auto Tag run died with HTTP 401.
--
-- 'doubao-seed-2-0-lite-260428' is the platform catalog entry
-- `mediahub-doubao-seed-2-0-lite` (doubao, multimodal, last_test_status = ok) —
-- the same model the system `analyze` agent already runs on, so it needs no
-- user-supplied credentials.
--
-- Idempotent: the WHERE clause pins the old model, so re-running is a no-op and
-- any agent an operator has since re-pointed by hand is left alone.

UPDATE ai_agents
SET model = 'doubao-seed-2-0-lite-260428', updated_at = now()
WHERE slug IN ('caption', 'classify')
  AND model = 'Qwen/Qwen3-VL-235B-A22B-Instruct';
