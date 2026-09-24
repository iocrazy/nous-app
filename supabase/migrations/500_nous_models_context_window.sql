-- 500: nous_models.context_window_tokens — model context windows from the catalog.
--
-- Why
-- ===
-- agent_framework.context_window.resolve_model_window only knew a hardcoded
-- table (_MODEL_WINDOWS). Production 2026-09-23: every model actually run in
-- the last 30 days (doubao-seed-2-0-lite-260428 ×194 runs, deepseek-v4-*) was
-- missing from it, so every compaction tier divided by the generic 28000-token
-- fallback and the step-end context gauge never fired (it skips when the
-- window is not known). A context window is a model fact the admin owns, so
-- it lives on the catalog row; the table becomes the second layer.
--
-- Lookup key
-- ==========
-- ai_agents.model / agent_runs.model store the ACTUAL model id
-- (e.g. doubao-seed-2-0-lite-260428), so the resolver indexes by
-- actual_model as well as by name. Rows that disagree about one key take the
-- smaller window (backend/app/agent_framework/catalog_windows.py).
--
-- NULL = unknown, never 0
-- =======================
-- NULL falls through to the table, then the fallback. The CHECK keeps 0 and
-- negatives out: a zero denominator is not "unknown", it is a crash.
--
-- Seed — only what this migration can stand behind
-- ================================================
-- * doubao-seed-2-0-lite%: 131072. Public Volcengine docs list 256k for the
--   Seed 2.0 family; that is UNVERIFIED against the console, so this is the
--   conservative value. The admin may change it. (Same value is the table's
--   second-layer entry for doubao-seed-2-0-lite-260428.)
-- * nous-engine qwen (qwen3-8-27b): the window is vLLM's --max-model-len in the
--   nous-engine deploy, not knowable from here. Left NULL; the admin probe can
--   read /v1/models max_model_len later.
-- * deepseek-v4-*, doubao-seed-2-0-pro: not recorded anywhere — left NULL.
--
-- Only rows still NULL are seeded, so a re-run never overwrites an admin edit.
-- Idempotent: ADD COLUMN IF NOT EXISTS + DROP/ADD CHECK.

BEGIN;

ALTER TABLE public.nous_models
  ADD COLUMN IF NOT EXISTS context_window_tokens integer;

ALTER TABLE public.nous_models
  DROP CONSTRAINT IF EXISTS nous_models_context_window_tokens_check;

ALTER TABLE public.nous_models
  ADD CONSTRAINT nous_models_context_window_tokens_check
  CHECK (context_window_tokens IS NULL OR context_window_tokens > 0);

COMMENT ON COLUMN public.nous_models.context_window_tokens IS
  'Model context window in tokens. First layer of resolve_model_window '
  '(then the hardcoded _MODEL_WINDOWS table, then LLM_MAX_CONTEXT_TOKENS). '
  'NULL = unknown. Admin-owned; see migration 500.';

-- Unverified, conservative; admin may change (public docs say 256k).
UPDATE public.nous_models
   SET context_window_tokens = 131072
 WHERE actual_model LIKE 'doubao-seed-2-0-lite%'
   AND context_window_tokens IS NULL;

COMMIT;
