-- ai_model_prices: canonical pricing per (model, provider), versioned by
-- effective_at. Used at run-start to snapshot cost rates onto agent_runs,
-- so admin edits never rewrite history (finding #4).
--
-- Unit: prompt_cents_per_1k_tokens / completion_cents_per_1k_tokens in US cents.
CREATE TABLE ai_model_prices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model TEXT NOT NULL,
  provider TEXT NOT NULL,
  prompt_cents_per_1k NUMERIC(12, 6) NOT NULL,
  completion_cents_per_1k NUMERIC(12, 6) NOT NULL,
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (model, provider, effective_at)
);

CREATE INDEX idx_ai_model_prices_lookup
  ON ai_model_prices (model, provider, effective_at DESC);

-- Seed common model prices as of 2026-04 (US cents per 1k tokens).
-- These are best-effort reference values; admin can INSERT new rows with
-- newer effective_at to shift pricing without rewriting history.
INSERT INTO ai_model_prices (model, provider, prompt_cents_per_1k, completion_cents_per_1k)
VALUES
  ('qwen-max',        'qwen',      0.400, 1.200),
  ('qwen-plus',       'qwen',      0.080, 0.240),
  ('qwen-turbo',      'qwen',      0.030, 0.060),
  ('gpt-4o',          'openai',    0.500, 2.000),
  ('gpt-4o-mini',     'openai',    0.015, 0.060),
  ('claude-opus-4',   'anthropic', 1.500, 7.500),
  ('claude-sonnet-4', 'anthropic', 0.300, 1.500),
  ('claude-haiku-4',  'anthropic', 0.025, 0.125),
  ('deepseek-chat',   'deepseek',  0.027, 0.110),
  ('deepseek-reasoner','deepseek', 0.055, 0.219),
  ('doubao-pro',      'doubao',    0.080, 0.200);

-- RLS: everyone reads (pricing is not secret), service role writes.
ALTER TABLE ai_model_prices ENABLE ROW LEVEL SECURITY;
CREATE POLICY "prices_readable_by_all" ON ai_model_prices FOR SELECT
  USING (true);
CREATE POLICY "prices_write_service_only" ON ai_model_prices FOR ALL TO service_role
  USING (true) WITH CHECK (true);
