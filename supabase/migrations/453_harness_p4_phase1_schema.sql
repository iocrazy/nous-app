-- 453: harness 第四轮 · 第 1 期骨架 schema 一次到位
-- spec: docs/superpowers/specs/2026-09-05-harness-p4-task-visibility-control-design.md §3
--
-- 五件事，都是"迁移先行、消费代码后续 PR"：
--   1. transcript 事件白名单放行 step_start / step_end / inbox_claimed / deliverable / budget_check
--   2. 事件加坐标列 turn / step（旧行 NULL）—— fold(events[:seq]) 可重放到任意一步的前提
--   3. 新表 agent_run_inbox：收件箱是唯一队列，按持久目标（issue / conversation）键控
--   4. agent_runs.pause_requested / fork_of_run_id / fork_at_seq；issues.paused_at / budget_cents；
--      conversation_ai_meta.paused_at
--   5. 新表 run_deliverables：产出登记（第 3 期启用咽喉点，表先建）
BEGIN;

-- 1. 事件白名单（照 443 的 DROP/ADD 写法）
ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;
ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text, 'assistant'::text, 'tool_call'::text, 'error'::text, 'system'::text,
    'llm_retry'::text, 'todo_write'::text,
    'compaction_start'::text, 'compaction_summary'::text, 'compaction_end'::text,
    'turn_end'::text,
    'step_start'::text, 'step_end'::text, 'inbox_claimed'::text,
    'deliverable'::text, 'budget_check'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436: llm_retry. 443: todo_write, compaction_*, turn_end. '
  '453: step_start/step_end (per-LLM-call brackets with usage+cost — the replay-to-any-seq '
  'precondition), inbox_claimed (runner picked up an inbox item at a step boundary), '
  'deliverable (output registered through the single choke point), budget_check (budget hook).';

-- 2. 坐标列
ALTER TABLE public.agent_run_transcript_events
  ADD COLUMN IF NOT EXISTS turn INTEGER,
  ADD COLUMN IF NOT EXISTS step INTEGER;
COMMENT ON COLUMN public.agent_run_transcript_events.turn IS
  '453: turn coordinate (dsh Location). NULL on rows written before 453.';
COMMENT ON COLUMN public.agent_run_transcript_events.step IS
  '453: step coordinate = one LLM call plus the tool executions it requested.';

-- 3. 收件箱
CREATE TABLE IF NOT EXISTS public.agent_run_inbox (
  id            BIGINT PRIMARY KEY DEFAULT public.generate_snowflake_id(),
  target_kind   TEXT   NOT NULL CHECK (target_kind IN ('conversation', 'issue')),
  target_id     BIGINT NOT NULL,
  user_id       UUID   NOT NULL,
  kind          TEXT   NOT NULL CHECK (kind IN ('steer', 'answer', 'pause', 'resume', 'budget_reply')),
  content       JSONB  NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at    TIMESTAMPTZ,
  claimed_run_id BIGINT REFERENCES public.agent_runs(id) ON DELETE SET NULL,
  claimed_turn  INTEGER,
  claimed_step  INTEGER,
  expired_at    TIMESTAMPTZ
);
COMMENT ON TABLE public.agent_run_inbox IS
  '453: the ONE queue into a running agent (dsh: the Agent inbox is the only queue). Keyed by the '
  'durable target (issue / conversation), not by run — a run is one turn; the target outlives it. '
  'Unclaimed = claimed_at IS NULL. The root run claims at each step boundary (SKIP LOCKED); '
  'issue comments posted while a root run is live are mirrored here as steer.';
CREATE INDEX IF NOT EXISTS idx_agent_run_inbox_pending
  ON public.agent_run_inbox (target_kind, target_id, created_at)
  WHERE claimed_at IS NULL AND expired_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_agent_run_inbox_run ON public.agent_run_inbox (claimed_run_id);

ALTER TABLE public.agent_run_inbox ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_run_inbox_own_readable ON public.agent_run_inbox;
CREATE POLICY agent_run_inbox_own_readable ON public.agent_run_inbox
  FOR SELECT USING (user_id = auth.uid());
DROP POLICY IF EXISTS agent_run_inbox_service_role_all ON public.agent_run_inbox;
CREATE POLICY agent_run_inbox_service_role_all ON public.agent_run_inbox
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Realtime：前端要看到 pending → claimed 的变化（照 452 的写法；守卫脚本会盯）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime')
     AND NOT EXISTS (SELECT 1 FROM pg_publication_tables
                     WHERE pubname = 'supabase_realtime' AND schemaname = 'public'
                       AND tablename = 'agent_run_inbox') THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.agent_run_inbox;
  END IF;
END
$$;

-- 4. 运行 / 目标级控制列
ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS pause_requested BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS fork_of_run_id BIGINT REFERENCES public.agent_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS fork_at_seq INTEGER;
COMMENT ON COLUMN public.agent_runs.pause_requested IS
  '453: pause signal for the live run (sibling of cancel_requested). Paused-ness itself lives on '
  'the target: issues.paused_at / conversation_ai_meta.paused_at.';
COMMENT ON COLUMN public.agent_runs.fork_of_run_id IS
  '453: this run was forked from that run''s events[:fork_at_seq] (phase 2 replay/fork).';

ALTER TABLE public.issues
  ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS budget_cents INTEGER CHECK (budget_cents IS NULL OR budget_cents >= 0);
COMMENT ON COLUMN public.issues.paused_at IS
  '453: target-level pause. Non-NULL blocks autopilot/routine re-dispatch; resume clears it.';
COMMENT ON COLUMN public.issues.budget_cents IS
  '453: spend cap for this issue''s runs; NULL = unlimited. 80% warns, 100% halts (phase 2).';

ALTER TABLE public.conversation_ai_meta
  ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;

-- 5. 产出登记（第 3 期启用；表先建，让 deliverable 事件有落点）
CREATE TABLE IF NOT EXISTS public.run_deliverables (
  id             BIGINT PRIMARY KEY DEFAULT public.generate_snowflake_id(),
  run_id         BIGINT NOT NULL REFERENCES public.agent_runs(id) ON DELETE CASCADE,
  seq            INTEGER,
  kind           TEXT   NOT NULL,
  ref_id         TEXT   NOT NULL,
  version        INTEGER NOT NULL DEFAULT 1,
  parent_version INTEGER,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.run_deliverables IS
  '453: every output an agent produces, registered through register_deliverable() — the single '
  'choke point. Not registered = does not exist. (kind, ref_id) points at the asset/canvas/publish '
  'row; version chains v1 → v2 after a steer.';
CREATE INDEX IF NOT EXISTS idx_run_deliverables_run ON public.run_deliverables (run_id);
CREATE INDEX IF NOT EXISTS idx_run_deliverables_ref ON public.run_deliverables (kind, ref_id);
ALTER TABLE public.run_deliverables ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS run_deliverables_service_role_all ON public.run_deliverables;
CREATE POLICY run_deliverables_service_role_all ON public.run_deliverables
  FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMIT;
