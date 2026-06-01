-- Migration 248: cached-token accounting (paperclip cost port Phase 1a)
--
-- mediahub costs every prompt token at the full prompt rate, ignoring provider
-- prompt-caching (OpenAI cached_tokens / DeepSeek cache_hit / Qwen). Borrow
-- paperclip's cost_events.cached_input_tokens model: track cached input tokens
-- and (optionally) price them cheaper.
--
-- Additive + no billing regression: cached_input_cents_per_1k is NULLABLE; when
-- NULL the recorder bills cached tokens at prompt_cents_per_1k exactly as today.
-- The discount only activates once a per-model cached rate is filled in.

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS cached_input_tokens integer NOT NULL DEFAULT 0;

ALTER TABLE public.ai_model_prices
  ADD COLUMN IF NOT EXISTS cached_input_cents_per_1k numeric;
