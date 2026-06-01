-- Rollback for migration 248. (CI auto-detect skips *_rollback.sql.)
ALTER TABLE public.ai_model_prices DROP COLUMN IF EXISTS cached_input_cents_per_1k;
ALTER TABLE public.agent_runs DROP COLUMN IF EXISTS cached_input_tokens;
