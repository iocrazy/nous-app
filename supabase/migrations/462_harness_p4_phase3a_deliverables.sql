-- 462: harness 第五轮 · 三期 3a —— 产出登记（血缘列与唯一约束）
-- spec: docs/superpowers/specs/2026-09-10-harness-p4-phase3a-outputs-lineage-design.md §3
-- 表 run_deliverables 与事件类型 'deliverable' 在 453 已就位（461 最后一次重写
-- 事件白名单）；本迁移**不碰事件白名单**，只补血缘列与两个索引，外加一张
-- 2b-2 遗留小票（§7-2）。照 459/460/461 的 DROP/ADD 幂等写法；
-- 不 SET ROLE（以连接角色 postgres 跑）。
BEGIN;

-- (a) 血缘列 -------------------------------------------------------
-- 全部可空：存量零行（453 建表至今无人写过），但按可空写才允许将来补登记
-- 历史产出——那些行本就没有 turn/step 坐标可填。
ALTER TABLE public.run_deliverables
  ADD COLUMN IF NOT EXISTS title      TEXT,
  ADD COLUMN IF NOT EXISTS model      TEXT,
  ADD COLUMN IF NOT EXISTS cost_cents NUMERIC(12,4),
  ADD COLUMN IF NOT EXISTS turn       INTEGER,
  ADD COLUMN IF NOT EXISTS step       INTEGER;

COMMENT ON COLUMN public.run_deliverables.title IS
  '462: 展示用标题，登记时截断至 120 字符（与 inbox 的 clip 同族，事件与列同一份）。';
COMMENT ON COLUMN public.run_deliverables.model IS
  '462: 产出这一版的模型名（provider 前缀原样保留）。';
COMMENT ON COLUMN public.run_deliverables.cost_cents IS
  '462: 这一版产出自身的花费；run 级总账仍在 agent_runs.cost_cents，两者不互相推导。';
COMMENT ON COLUMN public.run_deliverables.turn IS
  '462: 登记发生在哪一轮（与转写事件的 turn/step 是同一套坐标）。';
COMMENT ON COLUMN public.run_deliverables.step IS
  '462: 登记发生在哪一步（一次 LLM 调用 + 它请求的工具执行）。';

-- (b) 版本唯一：并发登记同一对象必须有一个失败并重算 -----------------
-- 作用域必须带上 version。只锁 (kind, ref_id) 会把「steer 之后的 v2」变成
-- 冲突，而版本链正是本期要记的东西。
CREATE UNIQUE INDEX IF NOT EXISTS run_deliverables_kind_ref_version_key
  ON public.run_deliverables (kind, ref_id, version);

-- (c) 取最新版 / 读版本链 -------------------------------------------
CREATE INDEX IF NOT EXISTS idx_run_deliverables_ref_latest
  ON public.run_deliverables (kind, ref_id, version DESC);

-- (d) 小票（2b-2 遗留，spec §7-2）：收件箱 dedupe 的并发唯一索引 ------
-- dedupe_key 住在 content jsonb 里（无列、无迁移是当时的刻意选择）；
-- 唯一性只对 LIVE 行成立：expired_at 非空的行没人消费过，允许重新排队，
-- 与 dedupe_lookup_stmt 的 WHERE expired_at IS NULL 严格同口径。
CREATE UNIQUE INDEX IF NOT EXISTS agent_run_inbox_dedupe_live_key
  ON public.agent_run_inbox (target_kind, target_id, (content->>'dedupe_key'))
  WHERE content ? 'dedupe_key' AND expired_at IS NULL;

COMMIT;
