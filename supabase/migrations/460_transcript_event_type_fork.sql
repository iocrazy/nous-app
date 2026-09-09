-- 460: harness 第四轮 · 二期 2b-1 —— fork 落到 transcript
-- spec: docs/superpowers/specs/2026-09-09-harness-p4-phase2b1-replay-fork-timeout-design.md §2.4
-- 只做一件事（迁移先行、消费代码后续 PR）：
--   transcript 事件白名单放行 fork（分叉 run 的首条事件 {of_run_id, at_seq, steer}）。
-- 逐工具超时不加类型：timed_out 走 tool_call.payload.result。
-- 照 459 的 DROP/ADD 幂等写法；不 SET ROLE（以连接角色 postgres 跑）。
BEGIN;

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
    'fork'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436: llm_retry. 443: todo_write, compaction_*, turn_end. '
  '453: step_start/step_end (per-LLM-call brackets with usage+cost — the replay-to-any-seq '
  'precondition), inbox_claimed (runner picked up an inbox item at a step boundary), '
  'deliverable (output registered through the single choke point), budget_check (budget hook). '
  '459: question_asked, question_answered, capability_denied. '
  '460: fork (first event of a forked run: {of_run_id, at_seq, steer} — phase 2b-1).';

COMMIT;
