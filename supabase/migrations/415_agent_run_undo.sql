-- 415_agent_run_undo.sql
--
-- Agent Run 撤销立项（spec: docs/superpowers/specs/2026-08-09-agent-run-undo-design.md）
-- 三件套：
--   1. script_shots.created_by_agent_run_id — agent 建卡归属列。只在 agent
--      create_shot 时写；人写 / Auto-Storyboard 的卡保持 NULL。
--   2. script_shot_ops — shot 写入账本，与 script_ops 平行、互不侵入：
--      script_ops.op_seq == content_version 的 watermark 语义是 P1 版本控制
--      的核心不变量，shot 写入不碰 content_version，塞进去会破坏全链。
--   3. agent_runs.undone_at — run 级一次性撤销标记，驱动前端按钮态与端点幂等。
--
-- agent_runs.id 是 BIGINT snowflake（mig 232），不是 UUID。
--
-- RLS：script_shot_ops 不启用。它是后端内部账本（不经 PostgREST 暴露，
-- 只有服务端超管连接读写）；剧本域租户策略（mig 408 起的第三层）后续分期
-- 覆盖时再一并处理，现在启用反而会挡住 CI ephemeral 库的非平台角色。

ALTER TABLE public.script_shots
  ADD COLUMN IF NOT EXISTS created_by_agent_run_id BIGINT NULL
  REFERENCES public.agent_runs(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS public.script_shot_ops (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  run_id BIGINT NOT NULL REFERENCES public.agent_runs(id) ON DELETE CASCADE,
  shot_id BIGINT NOT NULL REFERENCES public.script_shots(id) ON DELETE CASCADE,
  -- 冗余列，供 scene 级查询；不加 FK（shot 删除时行随 CASCADE 消失，scene
  -- 维度只做过滤，不需要参照完整性）。
  scene_id BIGINT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('create', 'update')),
  -- update 时旧值快照（仅本次涉及字段）；create 为 NULL。
  before_json JSONB NULL,
  -- 写完后的字段快照：create 为全部 _WRITABLE_SHOT_FIELDS（显式含 NULL），
  -- update 仅本次涉及字段。
  after_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_shot_ops_run
  ON public.script_shot_ops (run_id);

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS undone_at TIMESTAMPTZ NULL;
