-- 459: harness 第四轮 · 二期 2a —— 类型化提问落到 transcript
-- spec: docs/superpowers/specs/2026-09-06-harness-p4-phase2a-control-plane-design.md §5
--
-- 只做一件事（迁移先行、消费代码后续 PR）：
--   transcript 事件白名单放行 question_asked / question_answered
--   （AskUser 工具提问、用户回答；折叠 folds/question.py 与 emit 在 Task 2 接上）
--   顺带放行 capability_denied：agent_runner.py 早已 emit 它（能力门 abort，每 turn 每工具一条），
--   但从 397 起没有任何一版 CHECK 收过它，best-effort 写入一直被静默拒绝（生产表零行）。
-- 无新列：paused_at / pause_requested 已由 453 落地；question 的 options / kind 进既有 jsonb。
-- 照 443/453 的 DROP/ADD 幂等写法；不 SET ROLE（以连接角色 postgres 跑）。
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
    'capability_denied'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436: llm_retry. 443: todo_write, compaction_*, turn_end. '
  '453: step_start/step_end (per-LLM-call brackets with usage+cost — the replay-to-any-seq '
  'precondition), inbox_claimed (runner picked up an inbox item at a step boundary), '
  'deliverable (output registered through the single choke point), budget_check (budget hook). '
  '459: question_asked (AskUser / FinishIssue options — typed question to a human), '
  'question_answered (the human picked a label; answer travels as a normal message with answer_to). '
  '459 also admits capability_denied: agent_runner has emitted it since the capability gate landed, '
  'but no CHECK ever allowed it, so every such best-effort INSERT was silently rejected.';

COMMIT;
