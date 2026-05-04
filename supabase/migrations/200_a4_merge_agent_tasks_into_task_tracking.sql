-- 200_a4_merge_agent_tasks_into_task_tracking.sql
--
-- A4: 合并 agent_tasks 到 task_tracking（用 task_kind 列区分）
--
-- 设计决策（见 docs/plans/2026-05-04-a-route-handoff.md §5 + 本 session 修订）:
--   * 放弃 task_tracking_dbos_fk —— task_tracking 从"DBOS sidecar"
--     升级为"通用任务追踪表"，DBOS 只是 task_kind='workflow' 的特例。
--   * 不动 agent_inbox / agent_outbox 的 actor mailbox 模式（列结构不重叠，
--     特化索引价值高）。它们的 task_id FK 改成 nullable + 取消 FK 约束，
--     应用层自己映射到 task_tracking.dbos_workflow_id (TEXT)。
--   * task_tracking PK 是 TEXT (dbos_workflow_id)；新增的 parent_task_id /
--     root_task_id 也是 TEXT，self-FK 指 dbos_workflow_id；agent_id /
--     inbox_message_id 仍是 UUID（指向 ai_agents / agent_inbox）。
--   * lifecycle_status 8 状态降到 5 状态会丢精度
--     (assigned / waiting_for_other / blocked) → 把原值放进 phase 列保留。
--
-- 回滚：见 200_a4_rollback.sql（DROP 新列、重新加 dbos FK、agent_tasks
-- 表如果还没 drop 直接保留）。本 migration 不 DROP agent_tasks，等回归
-- 跑稳 7 天再做（见交接文档）。

BEGIN;

-- ================================================================
-- Step 1: drop dbos cascade FK（核心放弃）
-- ================================================================
ALTER TABLE public.task_tracking DROP CONSTRAINT IF EXISTS task_tracking_dbos_fk;

-- ================================================================
-- Step 2: 加 5 个新列
-- ================================================================
ALTER TABLE public.task_tracking
  ADD COLUMN IF NOT EXISTS task_kind        TEXT NOT NULL DEFAULT 'workflow'
    CHECK (task_kind IN ('workflow', 'agent_task')),
  ADD COLUMN IF NOT EXISTS agent_id         UUID REFERENCES public.ai_agents(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS parent_task_id   TEXT,
  ADD COLUMN IF NOT EXISTS root_task_id     TEXT,
  ADD COLUMN IF NOT EXISTS inbox_message_id UUID;

-- self-FK: parent / root 指向 dbos_workflow_id (TEXT)
-- 用 DEFERRABLE 让数据迁移可以一次 INSERT 完整子树
ALTER TABLE public.task_tracking
  ADD CONSTRAINT task_tracking_parent_fk
    FOREIGN KEY (parent_task_id) REFERENCES public.task_tracking(dbos_workflow_id) ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE public.task_tracking
  ADD CONSTRAINT task_tracking_root_fk
    FOREIGN KEY (root_task_id) REFERENCES public.task_tracking(dbos_workflow_id) ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

-- inbox_message_id → agent_inbox.id (UUID, simple)
ALTER TABLE public.task_tracking
  ADD CONSTRAINT task_tracking_inbox_fk
    FOREIGN KEY (inbox_message_id) REFERENCES public.agent_inbox(id) ON DELETE SET NULL;

-- ================================================================
-- Step 3: 索引
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_task_tracking_task_kind
  ON public.task_tracking(task_kind);

CREATE INDEX IF NOT EXISTS idx_task_tracking_agent
  ON public.task_tracking(agent_id, created_at DESC)
  WHERE agent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_tracking_parent
  ON public.task_tracking(parent_task_id)
  WHERE parent_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_tracking_root
  ON public.task_tracking(root_task_id)
  WHERE root_task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_tracking_agent_active
  ON public.task_tracking(agent_id, created_at DESC)
  WHERE task_kind = 'agent_task' AND status IN ('pending', 'processing');

-- ================================================================
-- Step 4: 数据迁移 agent_tasks → task_tracking
-- ================================================================
-- 注意：
--   * dbos_workflow_id = agent_tasks.id::text（沿用原 UUID 文本，避免下游断链）
--   * task_type = 'agent_task'（task_tracking.task_type 是 VARCHAR(20) 业务标签）
--   * status 5 状态映射 + phase 保留 8 状态原值
--   * parent_task_id / root_task_id ::text 转换
--   * metadata 兜住 8 状态丢失 + agent 私有字段（payload / result / current_run_id）
--   * title NOT NULL → COALESCE(title, 'Agent task')
--   * ON CONFLICT DO NOTHING：极小概率 UUID 字符串撞 dbos 现有 uuid，跳过

INSERT INTO public.task_tracking (
  dbos_workflow_id,
  user_id,
  task_type,
  status,
  phase,
  title,
  metadata,
  error_msg,
  created_at,
  started_at,
  completed_at,
  updated_at,
  task_kind,
  agent_id,
  parent_task_id,
  root_task_id,
  inbox_message_id
)
SELECT
  at.id::text                                                        AS dbos_workflow_id,
  at.user_id                                                          AS user_id,
  'agent_task'                                                        AS task_type,
  CASE at.lifecycle_status
    WHEN 'queued'              THEN 'pending'
    WHEN 'assigned'            THEN 'pending'
    WHEN 'in_progress'         THEN 'processing'
    WHEN 'waiting_for_other'   THEN 'processing'
    WHEN 'blocked'             THEN 'processing'
    WHEN 'done'                THEN 'completed'
    WHEN 'failed'              THEN 'failed'
    WHEN 'cancelled'           THEN 'cancelled'
    ELSE 'pending'
  END                                                                 AS status,
  at.lifecycle_status                                                 AS phase,
  COALESCE(NULLIF(TRIM(at.title), ''), 'Agent task')                  AS title,
  jsonb_build_object(
    'agent_payload',  at.payload,
    'agent_result',   at.result,
    'current_run_id', at.current_run_id,
    'error_code',     at.error_code,
    'assigned_at',    at.assigned_at,
    'ended_at',       at.ended_at
  )                                                                   AS metadata,
  at.error_message                                                    AS error_msg,
  at.created_at                                                       AS created_at,
  at.started_at                                                       AS started_at,
  at.ended_at                                                         AS completed_at,
  at.updated_at                                                       AS updated_at,
  'agent_task'                                                        AS task_kind,
  at.agent_id                                                         AS agent_id,
  at.parent_task_id::text                                             AS parent_task_id,
  at.root_task_id::text                                               AS root_task_id,
  at.inbox_message_id                                                 AS inbox_message_id
FROM public.agent_tasks at
WHERE NOT EXISTS (
  SELECT 1 FROM public.task_tracking tt
  WHERE tt.dbos_workflow_id = at.id::text
)
ON CONFLICT (dbos_workflow_id) DO NOTHING;

-- ================================================================
-- Step 5: 解开 agent_inbox / agent_outbox / agent_state_history
--         对 agent_tasks 的 FK（应用层改读 task_tracking）
-- ================================================================
ALTER TABLE public.agent_inbox
  DROP CONSTRAINT IF EXISTS agent_inbox_task_id_fkey;

ALTER TABLE public.agent_outbox
  DROP CONSTRAINT IF EXISTS agent_outbox_task_id_fkey;

ALTER TABLE public.agent_state_history
  DROP CONSTRAINT IF EXISTS agent_state_history_task_id_fkey;

-- agent_inbox.task_id / agent_outbox.task_id / agent_state_history.task_id
-- 仍是 UUID 列，应用层读时 cast 成 TEXT 查 task_tracking.dbos_workflow_id。
-- 不强制 FK，因为 UUID → TEXT 强制转换+CASCADE 在 PG 里需要写 trigger。

COMMENT ON COLUMN public.agent_inbox.task_id IS
  'UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。';

COMMENT ON COLUMN public.agent_outbox.task_id IS
  'UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。';

COMMENT ON COLUMN public.agent_state_history.task_id IS
  'UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。';

-- ================================================================
-- Step 6: 表级注释（语义升级声明）
-- ================================================================
COMMENT ON TABLE public.task_tracking IS
  '通用任务追踪表（multi-task_kind）。
   task_kind=''workflow''   → DBOS workflow，1:1 mirror dbos.workflow_status，
                              status 由 mirror_dbos_lifecycle_to_tracking trigger 写
   task_kind=''agent_task'' → 应用层（agent_workforce）维护 lifecycle，
                              不依赖 dbos.workflow_status；
                              phase 列保留 8 状态精度 (queued / assigned /
                              in_progress / waiting_for_other / blocked /
                              done / failed / cancelled)
   原 1:1 sidecar FK (task_tracking_dbos_fk) 在 A4 (migration 200) 移除，
   cascade GC 失效 — 由应用层负责清理过期任务。';

COMMENT ON COLUMN public.task_tracking.task_kind IS
  'workflow | agent_task — 决定 status 列的写入权（trigger vs 应用层）';

COMMENT ON COLUMN public.task_tracking.agent_id IS
  'task_kind=agent_task 时填，引用 ai_agents.id；workflow 时为 NULL';

COMMENT ON COLUMN public.task_tracking.parent_task_id IS
  '父任务的 dbos_workflow_id (TEXT)，跨 task_kind 通用；DBOS 自身 parent_workflow_id 仍存在 dbos.workflow_status，本列是应用层快查路径';

COMMENT ON COLUMN public.task_tracking.root_task_id IS
  '根任务的 dbos_workflow_id (TEXT)，跨 task_kind 通用';

COMMENT ON COLUMN public.task_tracking.inbox_message_id IS
  'task_kind=agent_task 时填，引用 agent_inbox.id（触发该 task 的入站消息）';

-- ================================================================
-- Step 7: 健康度 sanity check（迁完看一眼）
-- ================================================================
DO $$
DECLARE
  agent_task_count_old INT;
  agent_task_count_new INT;
BEGIN
  SELECT COUNT(*) INTO agent_task_count_old FROM public.agent_tasks;
  SELECT COUNT(*) INTO agent_task_count_new FROM public.task_tracking
    WHERE task_kind = 'agent_task';

  RAISE NOTICE 'A4 migration check: agent_tasks=%  → task_tracking[agent_task]=%',
    agent_task_count_old, agent_task_count_new;

  IF agent_task_count_old > 0 AND agent_task_count_new = 0 THEN
    RAISE WARNING 'A4 migration: agent_tasks 有 % 行但 task_tracking 没插进任何 agent_task 行，请人工核查',
      agent_task_count_old;
  END IF;
END$$;

COMMIT;

-- ================================================================
-- 后续步骤（不在本 migration 里）：
--   1. 应用层切表（agent_workforce_repository.py / agent_worker.py /
--      scheduler.py / inbox_processor.py / outbox_dispatcher.py）
--   2. 跑 7 天回归后另做 migration DROP TABLE public.agent_tasks CASCADE
--   3. 同时 DROP agent_state_history 的 trigger / 自动 audit
--      （或保留作 read-only audit log）
-- ================================================================
