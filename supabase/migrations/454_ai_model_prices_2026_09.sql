-- 454: price rows for every LLM that ran in the last 90 days but had none.
--
-- Why: RunRecorder snapshots ai_model_prices at run start; with no row,
-- cost_cents stays NULL, the Usage dashboard shows '—', and the harness P4
-- budget hook (80% warn / 100% halt, record-only in phase 1) can never fire.
-- 2026-09-05 audit of agent_runs (90d): only doubao-seed-2-0-pro-260215 had a
-- row. Missing: doubao-seed-2-0-lite-260428 (85 runs — the platform default),
-- deepseek-v4-flash (14), deepseek-v4-flash-vision-exp (4), deepseek-v4-pro (2),
-- Qwen/Qwen3-VL-235B-A22B-Instruct via modelscope (11), nous-qwen3-llm (5).
--
-- Unit (mig 146): US cents per 1k tokens. cents_per_1k = USD_per_1M / 10.
--
-- Sources (checked 2026-09-06):
-- * DeepSeek https://api-docs.deepseek.com/quick_start/pricing — list (peak)
--   price, USD/1M: v4-flash & v4-flash-vision-exp in 0.44 / cache-hit 0.014 /
--   out 1.32; v4-pro in 1.32 / cache-hit 0.044 / out 3.96. Off-peak is 50%
--   off, but peak = UTC 01–04 & 06–10 Mon–Fri = Beijing working hours, which
--   is when this team runs; snapshot the conservative figure.
-- * Volcengine Ark (cn-beijing, ≤32K input tier), CNY/1M:
--   Seed-2.0-lite in 0.6 / out 3.6 / cache 0.12; Seed-2.0-pro in 3.2 / out 16
--   / cache 0.64. Converted at 7.10 CNY/USD → cents_per_1k = CNY_per_1M / 71.
--   The existing pro row (0.08 / 0.20, mig 161/226) was a placeholder that
--   mirrored the old doubao-pro line; re-snapshotted here with a newer
--   effective_at — history is versioned, not rewritten (mig 146 convention).
-- * ModelScope API-Inference is a free tier (2000 req/day) → 0 / 0.
-- * nous-qwen3-llm is self-hosted (GPU box, catalog pricing_type=per_hour) →
--   0 / 0 per token. Runs record provider='' so the lookup is by model only.
--
-- supports_vision is set EXPLICITLY on every row: model_capabilities reads
-- `bool(row.supports_vision)`, and a DB row wins over the prefix heuristic —
-- a NULL here would silently strip images from a vision model.
--
-- Fixed effective_at so a re-run is a no-op under the (model, provider,
-- effective_at) unique key.
BEGIN;

INSERT INTO public.ai_model_prices
    (model, provider, prompt_cents_per_1k, completion_cents_per_1k,
     cached_input_cents_per_1k, supports_vision, effective_at)
VALUES
    -- DeepSeek V4 (USD list/peak price)
    ('deepseek-v4-flash',            'deepseek',   0.044000, 0.132000, 0.001400, FALSE, '2026-09-06T00:00:00Z'),
    ('deepseek-v4-flash-vision-exp', 'deepseek',   0.044000, 0.132000, 0.001400, TRUE,  '2026-09-06T00:00:00Z'),
    ('deepseek-v4-pro',              'deepseek',   0.132000, 0.396000, 0.004400, FALSE, '2026-09-06T00:00:00Z'),
    -- Volcengine Ark Doubao Seed 2.0 (CNY ≤32K tier @ 7.10)
    ('doubao-seed-2-0-lite-260428',  'doubao',     0.008451, 0.050704, 0.001690, TRUE,  '2026-09-06T00:00:00Z'),
    ('doubao-seed-2-0-pro-260215',   'doubao',     0.045070, 0.225352, 0.009014, TRUE,  '2026-09-06T00:00:00Z'),
    -- Free / self-hosted: a 0 row so cost_cents is 0, not NULL
    ('Qwen/Qwen3-VL-235B-A22B-Instruct', 'modelscope', 0, 0, NULL, TRUE,  '2026-09-06T00:00:00Z'),
    ('nous-qwen3-llm',               'nous',       0, 0, NULL, FALSE, '2026-09-06T00:00:00Z')
ON CONFLICT (model, provider, effective_at) DO NOTHING;

COMMIT;
