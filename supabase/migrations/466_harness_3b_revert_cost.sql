-- 466: harness 三期 3b —— 回退占号 + 媒体按次价（spec §2.1）。
--
-- 回退是「人手发起但要占号」的唯一动作：run_deliverables.run_id 与
-- script_shot_ops.run_id 原本都 NOT NULL REFERENCES agent_runs(id)，于是人手回退
-- 结构上写不进账本、也拿不到版本号。这里放宽两处 NOT NULL，各加一个具名 CHECK
-- 换回约束 —— 「既没有 run 也没有人」的行仍然不允许存在。
--
-- per_call_cents：图片/视频按次计价，沿用 ai_model_prices 这张「按 effective_at
-- 版本化、只追加不改」的表，不造第二张价格表。
--
-- generated_media(promoted_resource_id) 的反查索引不在这里：456 的 partial UNIQUE
-- uq_genmedia_promoted_resource 已经覆盖。（它是 307 的 idx_genmedia_promoted 的
-- **替换**而非并存——456 第 13 行把后者 DROP 了，注释原话 "is subsumed and
-- dropped"。再建第三个只是多一份写放大。）
BEGIN;

ALTER TABLE public.run_deliverables
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS actor_user_id UUID,
    ADD COLUMN IF NOT EXISTS reverted_from_version INTEGER,
    ADD COLUMN IF NOT EXISTS ledger_ref TEXT;

ALTER TABLE public.run_deliverables
    DROP CONSTRAINT IF EXISTS run_deliverables_run_or_actor;
ALTER TABLE public.run_deliverables
    ADD CONSTRAINT run_deliverables_run_or_actor
    CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL);

ALTER TABLE public.script_shot_ops
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS actor TEXT;

ALTER TABLE public.script_shot_ops
    DROP CONSTRAINT IF EXISTS script_shot_ops_run_or_actor;
ALTER TABLE public.script_shot_ops
    ADD CONSTRAINT script_shot_ops_run_or_actor
    CHECK (run_id IS NOT NULL OR actor IS NOT NULL);

ALTER TABLE public.ai_model_prices
    ADD COLUMN IF NOT EXISTS per_call_cents NUMERIC(12,4);

COMMIT;
NOTIFY pgrst, 'reload schema';
