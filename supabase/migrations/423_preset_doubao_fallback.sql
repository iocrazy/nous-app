-- 423_preset_doubao_fallback.sql
--
-- Provider 容错 P1（spec: docs/superpowers/specs/2026-08-11-provider-resilience-design.md §4）。
-- doubao-seed-2-0-pro 自 2026-08-08 持续 429,而所有 agent 的 fallback_models
-- 都是空数组("primary + 0 fallback(s)" 的真意)——链路机制(LLMFallbackChain,
-- mig 155)早就建好,只是没人配过。
--
-- 只动系统预设(summarize/analyze/coordinator 用此主模型)+ 只填空数组:
-- 不覆盖任何已手配值,不动用户自建 agent(他们模型自选,admin 后台可配)。
-- lite 有 ~14% 空产出史,但 pro 当前 0% 可用——降级明确优于不可用。
-- qwen 不进救援链:死过三周的引擎不进急救箱。
-- 幂等:重跑时空数组条件不再命中。

UPDATE public.ai_agents
SET fallback_models = ARRAY['doubao-seed-2-0-lite-260428']
WHERE model = 'doubao-seed-2-0-pro-260215'
  AND fallback_models = '{}'
  AND is_system_preset = true;
