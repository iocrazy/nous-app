-- 461: harness 第四轮 · 二期 2b-2 —— 编排（子代理 / 定时唤醒 / workforce 接活）
-- spec: docs/superpowers/specs/2026-09-10-harness-p4-phase2b2-orchestration-design.md §6
-- 五件事（a-e 见下方分节），消费代码在后续 Task。
-- 照 459/460 的 DROP/ADD 幂等写法；不 SET ROLE（以连接角色 postgres 跑）。
BEGIN;

-- (a) 事件白名单 ---------------------------------------------------
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
    'deliverable'::text, 'budget_check'::text,
    'question_asked'::text, 'question_answered'::text,
    'capability_denied'::text,
    'fork'::text,
    'subagent_spawned'::text, 'subagent_done'::text, 'schedule_set'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436/443/453/459 as before. 460: fork. '
  '461: subagent_spawned / subagent_done (written on the PARENT run, sync or '
  'async), schedule_set (agent armed a one-time issue wake-up) — phase 2b-2.';

-- (b) 收件箱 kind -------------------------------------------------
ALTER TABLE public.agent_run_inbox
  DROP CONSTRAINT IF EXISTS agent_run_inbox_kind_check;
ALTER TABLE public.agent_run_inbox
  ADD CONSTRAINT agent_run_inbox_kind_check
  CHECK (kind = ANY (ARRAY[
    'steer'::text, 'answer'::text, 'pause'::text, 'resume'::text,
    'budget_reply'::text,
    'subagent_result'::text
  ]));
COMMENT ON CONSTRAINT agent_run_inbox_kind_check ON public.agent_run_inbox IS
  '453: steer/answer/pause/resume/budget_reply. 461: subagent_result — a background '
  'sub-agent delivers its envelope to the parent through the ONE queue.';

-- (c) cron_expr 可空 ----------------------------------------------
ALTER TABLE public.user_schedules ALTER COLUMN cron_expr DROP NOT NULL;
ALTER TABLE public.user_schedules
  DROP CONSTRAINT IF EXISTS user_schedules_cron_or_once;
ALTER TABLE public.user_schedules
  ADD CONSTRAINT user_schedules_cron_or_once
  CHECK (
    cron_expr IS NOT NULL
    OR (task_type = 'issue_wakeup' AND coalesce(payload->>'once', '') = 'true')
  );
-- ⚠️ the coalesce is load-bearing, not tidiness. A payload with no `once` key
-- makes `payload->>'once'` NULL, so the bare comparison evaluates to NULL and
-- Postgres treats a NULL CHECK as SATISFIED — a cron-less issue_wakeup that
-- never declared itself one-shot would have been admitted, and nothing would
-- ever re-arm it. (`task_type='download'` was still rejected either way: FALSE
-- AND NULL is FALSE.) Verified both directions against a real Postgres.
COMMENT ON COLUMN public.user_schedules.cron_expr IS
  '5-field UTC cron. NULLABLE since 461: a one-time issue_wakeup gives next_fire_at '
  'directly and self-disables after firing. The cron_or_once CHECK keeps NULL out of '
  'every recurring task_type, where it would silently never re-arm.';

-- (d) 白名单曾允许这两种，但引擎注册表从不认：到点每分钟 skip 一次，永不执行。
--     禁掉存量行；T5 把白名单改成从注册表导出。
UPDATE public.user_schedules
   SET enabled = false, pause_reason = 'task_type_unsupported'
 WHERE task_type IN ('ai_transcription', 'ai_visual_analysis')
   AND enabled;

-- (e) mig 200 (A4) 第 234 行承诺的 DROP。仓库层只读写 task_tracking，零 SELECT
-- 命中此表；两个自引用 FK 随表消失，无外部依赖，不需要 CASCADE。
DROP TABLE IF EXISTS public.agent_tasks;
-- mig 159 built this trigger function for that table and nothing else ever
-- attached it; dropping the table leaves it orphaned, so it goes too.
DROP FUNCTION IF EXISTS public.bump_agent_tasks_updated_at();

COMMIT;
