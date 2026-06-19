-- 302 — Nous models: classify by model TYPE, not feature category.
--
-- The user-side "Nous provider" feature selects platform models by their
-- intrinsic model TYPE (one LLM serves many features), not by a feature
-- category. Rename category → type, swap the CHECK constraint to the type
-- enum, and add an admin usage-note column.
--
-- Prod `nous_models` is EMPTY (0 rows, verified 2026-06-19) so there is no
-- real remap. The defensive UPDATE below is a no-op on prod and only matters
-- if a non-prod env (dev) holds legacy rows — it keeps the ADD CONSTRAINT
-- from failing there. Apply order: drop old constraint → rename → remap →
-- add new constraint → add column → reindex.

ALTER TABLE public.nous_models DROP CONSTRAINT IF EXISTS nous_models_category_check;

ALTER TABLE public.nous_models RENAME COLUMN category TO type;

-- Defensive legacy→type remap (no-op on empty prod):
UPDATE public.nous_models SET type = 'asr' WHERE type = 'transcription';
UPDATE public.nous_models SET type = 'llm' WHERE type IN ('summarization', 'analysis');

ALTER TABLE public.nous_models
  ADD CONSTRAINT nous_models_type_check
  CHECK (type IN ('llm', 'embedding', 'tts', 'asr'));

ALTER TABLE public.nous_models ADD COLUMN IF NOT EXISTS description TEXT;

DROP INDEX IF EXISTS idx_nous_models_category;
CREATE INDEX IF NOT EXISTS idx_nous_models_type ON public.nous_models(type, is_enabled);
