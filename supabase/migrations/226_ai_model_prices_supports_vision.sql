-- Migration: 226_ai_model_prices_supports_vision
-- Description: Replaces the hardcoded _VISION_MODEL_PREFIXES whitelist in
-- app/agent_framework/multimodal.py with a per-row capability flag on
-- ai_model_prices. The lookup helper (Task 4b) reads this column at
-- startup; build_user_message (Task 4c) consults the helper instead of
-- the hardcoded prefix list. Fixes the doubao-seed-2-0-pro-260215 case
-- where vision was silently degraded to text placeholders because the
-- model id didn't match the doubao-vision* prefix.

ALTER TABLE public.ai_model_prices
    ADD COLUMN IF NOT EXISTS supports_vision BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.ai_model_prices.supports_vision IS
    'TRUE if the model accepts image_url multipart parts (OpenAI multimodal shape). Read by app.services.ai.model_capabilities.model_supports_vision.';

-- Backfill known-truthy rows seeded in migration 146.
UPDATE public.ai_model_prices SET supports_vision = TRUE
WHERE (model, provider) IN (
    ('gpt-4o', 'openai'),
    ('gpt-4o-mini', 'openai'),
    ('claude-opus-4', 'anthropic'),
    ('claude-sonnet-4', 'anthropic'),
    ('doubao-pro', 'doubao')
);

-- Seed new vision-capable rows not present in migration 146.
-- Pricing values are placeholder/best-effort — admin can refresh later
-- with new effective_at rows per the versioning convention in 146.
INSERT INTO public.ai_model_prices
    (model, provider, prompt_cents_per_1k, completion_cents_per_1k, supports_vision)
VALUES
    ('doubao-seed-2-0-pro-260215', 'doubao', 0.080, 0.200, TRUE),
    ('doubao-seed-2-0-pro',        'doubao', 0.080, 0.200, TRUE),
    ('doubao-vision-pro-32k',      'doubao', 0.500, 1.000, TRUE),
    ('qwen-vl-max',                'qwen',   0.300, 0.900, TRUE),
    ('qwen-vl-plus',               'qwen',   0.060, 0.180, TRUE),
    ('qwen2.5-vl-72b-instruct',    'qwen',   0.300, 0.900, TRUE),
    ('gpt-4o-2024-08-06',          'openai', 0.250, 1.000, TRUE),
    ('gpt-4-turbo',                'openai', 1.000, 3.000, TRUE),
    ('claude-3-opus-20240229',     'anthropic', 1.500, 7.500, TRUE),
    ('claude-3-5-sonnet-20241022', 'anthropic', 0.300, 1.500, TRUE),
    ('gemini-1.5-pro',             'gemini', 0.350, 1.050, TRUE),
    ('gemini-2.0-flash',           'gemini', 0.100, 0.400, TRUE)
ON CONFLICT (model, provider, effective_at) DO NOTHING;
